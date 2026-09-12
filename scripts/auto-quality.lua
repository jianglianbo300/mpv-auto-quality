-- auto-quality.lua v5.7 (2026-09-13 Nyx)
-- 单写者自动画质档 = 分辨率定基线（v4 路线，9-06 实测有效）+ 丢帧率闭环自适应（v5 新增）。
--
-- 为什么必须单写者: 外挂第二个按丢帧切档的脚本(auto-smooth.lua)会与 v4 抢同一批属性，
--   且 mpv 按文件名顺序加载脚本、后加载者赢 —— 4K 打开时 v4 轻量档会被覆盖回满血
--   （正中 9-06 实测 46% 丢帧的坑）。故升降档逻辑全部收进本脚本。
--
-- 版本演进:
--   v5    丢帧状态机并入 v4（单写者）
--   v5.1  每档必须列全 KEYS，否则降过应急档再回升时 scale 永久停在 fast_bilinear
--   v5.2  ① 切档函数不能叫 goto（mpv 内嵌 LuaJIT 保留字，v5 加载即语法错误）
--         ② mp.set_property 成功返回 nil、失败抛错 → 必须 pcall（否则每键误报警）
--         ③ 属性值统一字符串 yes/no（对齐 v4 实测路径）
--         ④ full 档 interpolation 改 "no" —— v4 的 full 表重开插帧，与 9-06 审计结论冲突
--            （桌面 GPU 被抢占时插帧是丢帧放大器；当时 mpv.conf 已改 interpolation=no，脚本漏改）
--   v5.3 恢复 v4 的 width 三路触发（file-loaded 时 width 可能仍为 0 → 4K 会误判成满血档）
--   v5.4 手动键 Ctrl+d / Ctrl+i 收进本脚本：按键时自动关掉自动档。
--        原因：这两个键改的正是本脚本托管的那批属性，而 enter() 每次换档会按
--        档位表整组重写 —— 手动改动会在下一次换档时被静默覆盖（本脚本是单写者，
--        键位就该归它管）。原绑定在 input.conf，已移出，见该文件注释。
--   v5.5 每次采样打一行 debug 日志（rate/drops/tier/baseline/stutter）。
--        目的：闭环的降档/回升到底有没有触发、阈值定得对不对，一直无从验证
--        （mpv 默认不写日志）。实测 mpv 的 --log-file 恒定按 debug 级写、
--        --msg-level 管不着它，所以 debug 级这行必然落进 mpv.log。
--        要精简单独关掉：--msg-level=auto_quality=no（该开关只影响终端/该模块）。
--   v5.6 ① 状态外露：内部状态写进 user-data/auto-quality/{tier,baseline,auto}，
--           外部（IPC）能直接读到真实档位。之前只能拿托管属性的值去反推，
--           遇到混合态会给出错误结论（实测：scale=ewa 但 dscale=bilinear）。
--        ② 漂移自愈：基线没变、但托管键被外部改动过 → 重新套用当前档位。
--           旧版 pick_baseline 只在「分辨率跨过 3800」时才动手，所以一旦
--           属性被外部改乱（IPC 直接 set / 别的脚本 / mpv 内置 b 键 cycle deband），
--           就会永久停在混合态，直到下次分辨率变化。这是实打实踩到的坑。
--   v5.7 换档后静默期（SETTLE_SECONDS）：真实播放日志暴露的振荡修复。
--        事故：720p 片源播到 0:33 触发降档 -> emergency，随后 34.1~37.5s 出现
--        libplacebo 重编译 shader（单次最慢 1199.7ms，日志 "translating HLSL to
--        DXBC (slow!)"）-> 编译期渲染停滞、frame-drop-count 暴涨（8 秒丢 134 帧）
--        -> 这些丢帧被当成"还在卡"继续计入 bad_streak/clean_accum。
--        更糟的是回升：emergency 干净 60s -> 回升 full -> full 档重型 shader
--        （ewa_lanczossharp+deband）重新编译 -> 编译期丢帧 -> 2~3 秒内又超阈值
--        -> 立刻降回 emergency。日志里 1:40 回升 / 1:42 复发、2:42 回升 / 2:45
--        复发，就是这么来的（full <-> emergency 振荡）。
--        结论：「改画质属性」本身有成本，而这个成本会被误判成「画质没救回来」。
--        修法：换档/起播后 N 秒内只记录采样、不参与判定（基准仍推进）。
--        副作用（已知、可接受）：真实卡顿的响应延迟 +N 秒。
--        附带现象：换档重建 filter chain 会让 frame-drop-count 归零，采样里出现
--        -30.54/s 这类负值（负值不会被判成 stutter，但会掩盖编译期真实丢帧）。
--
-- 硬件: i7-8550U + UHD620(带屏) + MX250 + 1080p SDR 屏。
-- ⚠ 坑(勿回退): mpv 0.41 + d3d11va 下 video-params/* 全读 nil，必须用别名 width。

local SAMPLE_INTERVAL  = 3      -- 秒: 丢帧采样周期
local DROP_THRESHOLD   = 1.0    -- 平均每秒丢 >=1 帧算卡(人眼可感)
local CONFIRM          = 2      -- 连续 N 周期超阈值才降档(防瞬时毛刺)
local CLEAN_SECONDS    = 60     -- 应急档连续 N 秒零丢帧才试探回升
local RECHECK_SECONDS  = 25     -- 回升后观察 N 秒, 立刻复发记一次失败
local COOLDOWN_SECONDS = 300    -- 连续 2 次回升失败 => 冷却 N 秒不再回升
local WIDTH_4K         = 3800   -- 4K 判定阈值（v4 沿用）
-- v5.7 静默期：换档/起播后 N 秒内只记录、不判定。
--   依据（实测日志）：换档到 frame-drop-count 稳定约需 9 秒
--   （33.8s 换档 -> 34.1~37.5s 编译 -> 42.7s 计数器归零）。取 10 留余量。
--   调小 => 真实卡顿反应快，但可能重新引入振荡；调大 => 更稳，但响应慢。
local SETTLE_SECONDS   = 10

-- 托管键: 每档必须显式给出每个键的值（单一事实源，防跨档残留）
local KEYS = { "scale", "cscale", "dscale", "linear-downscaling", "deband", "interpolation" }

local FULL = {                   -- 1080p 及以下: 放大场景, ewa_lanczossharp 双开(清晰度关键)
  scale = "ewa_lanczossharp", cscale = "ewa_lanczossharp", dscale = "mitchell",
  ["linear-downscaling"] = "yes", deband = "yes", interpolation = "no",
}
local LIGHT = {                  -- 4K 基线: 色度低频用 bilinear 无感; 降采样画质靠 dscale+linear
  scale = "ewa_lanczossharp", cscale = "bilinear", dscale = "mitchell",
  ["linear-downscaling"] = "yes", deband = "no", interpolation = "no",
}
local EMERGENCY = {              -- 基线仍喂不动: 砍掉 4K 降采样最大头(线性缩放)+缩放器降到最便宜
  -- v5.8 修正非法值：原写 scale="fast_bilinear" —— 那是【旧 vo=gpu 后端】的名字，
  --   gpu-next / libplacebo 下**非法**。后果：set 失败（用户真实日志里三次降档
  --   全部记 [f][cplayer] Invalid value for option scale: fast_bilinear），
  --   于是最重的 scale 根本没被换掉 —— **降档形同虚设**，这正是「降了还是卡」的原因。
  --   合法名（gpu-next，实测 mpv --scale=ZZZ 拿到的完整列表）：
  --     bilinear / bicubic_fast / oversample / spline16 / lanczos / mitchell / ...
  --   dscale 由 bilinear 改 oversample：专为降采样优化，实测更快
  --     （4K DV 300 帧：oversample 23.4fps vs bilinear 21.3fps）。
  scale = "bilinear", cscale = "bilinear", dscale = "oversample",
  ["linear-downscaling"] = "no", deband = "no", interpolation = "no",
}

local tier_table = { full = FULL, light = LIGHT, emergency = EMERGENCY }
local tier_label = { full = "满血", light = "4K轻量", emergency = "应急(已砍缩放)" }

-- v5.8 启动自检：档位表里的缩放器名必须是【当前 mpv 实际支持】的合法值。
--   为什么需要：一个非法名（如 v5.7 及以前的 fast_bilinear）会让该键的 set 静默失败，
--   而档位本身仍然"看起来"切换成功了（tier 变了、OSD 也提示了）—— 最隐蔽的一类 bug：
--   降档降了个寂寞，用户只觉得"降了还是卡"。
--   原理：mpv 把每个选项的合法值暴露在 option-info/<名>/choices（只读，不会改属性）。
--   缩放器类（scale/cscale/dscale）有 choices；flag 类（deband/interpolation/
--   linear-downscaling）没有，跳过。choices 随当前 VO 变化，所以换成旧 vo=gpu 时
--   fast_bilinear 会被正确判为合法 —— 自检自动适配后端。
local tiers_validated = false      -- 自检是否已真正执行（choices 可读才算数）
local function validate_tiers()
  if tiers_validated then return 0 end
  local bad, checked = 0, 0
  for name, t in pairs(tier_table) do
    for _, k in ipairs(KEYS) do
      local v = t[k]
      if type(v) == "string" and v ~= "yes" and v ~= "no" then
        local choices = mp.get_property_native("option-info/" .. k .. "/choices")
        if choices and #choices > 0 then
          checked = checked + 1
          local found = false
          for _, c in ipairs(choices) do
            if c == v then found = true; break end
          end
          if not found then
            bad = bad + 1
            mp.msg.error(string.format(
              "档位表非法值：%s.%s = %q —— mpv 不支持该值，此键会静默设置失败（降档失效！）",
              name, k, v))
          end
        end
      end
    end
  end
  -- 只有真的读到 choices 才算校验过；否则（VO 未就绪）等下一次触发重试
  if checked > 0 then
    tiers_validated = true
    if bad == 0 then
      mp.msg.debug(string.format("档位表自检通过：%d 个缩放器名均为当前后端的合法值", checked))
    end
  end
  return bad
end

local baseline = nil             -- 由片源分辨率决定 (full|light)
local tier = nil                 -- 当前生效档 (full|light|emergency)
local auto = true
local prev_drops, prev_time = nil, nil
local bad_streak, clean_accum = 0, 0
local upgrade_pending, fail_count, blocked_until = nil, 0, 0
local settle_until = 0           -- v5.7 静默期截止时刻（mp.get_time() 基准）

local function get_num(name)
  local ok, n = pcall(mp.get_property_number, name)
  if ok and n then return n end
  return nil
end

local function read_drops()
  return get_num("frame-drop-count")
      or get_num("decoder-frame-drop-count")
      or get_num("vo-drop-frame-count")
end

local function set1(k, v)
  local ok, err = pcall(mp.set_property, k, v)
  if not ok then mp.msg.warn("auto-quality: set " .. k .. " failed: " .. tostring(err)) end
end

-- v5.6 状态外露：把内部状态写进 user-data/*，供 IPC 直接读取。
-- 读法：python mpvctl.py get user-data/auto-quality/tier
-- 别再靠托管属性反推档位 —— 混合态下会误判。
local function publish()
  mp.set_property_native("user-data/auto-quality/tier", tier or "none")
  mp.set_property_native("user-data/auto-quality/baseline", baseline or "none")
  mp.set_property_native("user-data/auto-quality/auto", auto)
end

-- v5.6 托管键是否与某档一致（用于漂移自愈）。
-- 注意类型：scale/cscale/dscale 是字符串，其余三个是布尔。
local function keys_match(t)
  for _, k in ipairs(KEYS) do
    local cur = mp.get_property_native(k)
    local want = t[k]
    local same
    if type(want) == "string" and (want == "yes" or want == "no") then
      same = (cur == (want == "yes"))     -- "yes" -> true
    else
      same = (tostring(cur) == tostring(want))
    end
    if not same then
      mp.msg.verbose(string.format("drift: %s is %s, tier wants %s", k, tostring(cur), tostring(want)))
      return false
    end
  end
  return true
end

local function enter(new_tier, why)
  if tier == new_tier then return end
  tier = new_tier
  local t = tier_table[new_tier]
  for _, k in ipairs(KEYS) do set1(k, t[k]) end
  bad_streak, clean_accum = 0, 0
  settle_until = mp.get_time() + SETTLE_SECONDS   -- v5.7 换档自身有成本，先静默
  mp.msg.warn(string.format("AUTO-QUALITY -> %s (%s)", new_tier, why))
  mp.osd_message("画质档: " .. tier_label[new_tier] .. " (" .. why .. ")", 2)
  publish()
end

-- 分辨率 → 基线档。width 未知(0)时不改判，等 video-reconfig/observe 补触发。
local function pick_baseline(why)
  local w = get_num("width") or 0
  if w <= 0 then return end
  local nb = (w >= WIDTH_4K) and "light" or "full"
  if nb ~= baseline then
    baseline = nb
    tier = nil            -- 基线变了 => 允许重新套用（含从 emergency 回落到新基线）
    enter(nb, why .. " width=" .. tostring(w))
  elseif auto and tier and not keys_match(tier_table[tier]) then
    -- v5.6 自愈：基线未变但托管键被改乱 → 重新套用当前档位
    local keep = tier
    tier = nil
    enter(keep, "检测到托管键漂移，重新套用")
  end
  publish()
end

mp.register_event("file-loaded", function()
  prev_drops, prev_time = read_drops(), mp.get_time()
  bad_streak, clean_accum, upgrade_pending = 0, 0, nil
  fail_count, blocked_until = 0, 0
  -- v5.7 起播同样有首次全量 shader 编译（实测 5.9~9.4s 共约 2.8s），一并静默。
  -- 注意：同分辨率换片时 enter() 不会被调用，所以这行不能省。
  settle_until = mp.get_time() + SETTLE_SECONDS
  -- v5.8 档位表自检：此时 VO 已就绪，option-info/*/choices 可读（见 validate_tiers）
  validate_tiers()
  pick_baseline("新片源")
end)

-- v4 教训: file-loaded 时 width 常还没解析出来，必须三路触发兜底
mp.register_event("video-reconfig", function() pick_baseline("画面重配置") end)
mp.observe_property("width", "number", function() pick_baseline("宽高探测") end)

mp.add_periodic_timer(SAMPLE_INTERVAL, function()
  if not auto or not prev_time or not tier or not baseline then return end
  local now = mp.get_time()
  local drops = read_drops()
  if not drops then return end
  local dt = now - prev_time
  if dt <= 0 then return end
  local rate = (drops - prev_drops) / dt
  prev_drops, prev_time = drops, now

  -- v5.7 静默期：换档/起播后的 shader 重编译开销不算「卡顿」（见文件头 v5.7）。
  -- 基准 prev_drops 已在上面推进，所以静默期一结束就能立刻拿到正确的 3 秒增量。
  if now < settle_until then
    mp.msg.debug(string.format(
      "settle %.1fs left, skip judgement (rate=%.2f/s drops=%d tier=%s)",
      settle_until - now, rate, drops, tostring(tier)))
    return
  end

  local stutter = rate > DROP_THRESHOLD

  -- v5.5 采样落日志：标定 DROP_THRESHOLD 的唯一数据来源（见文件头 v5.5 说明）。
  mp.msg.debug(string.format(
    "sample rate=%.2f/s drops=%d dt=%.1fs tier=%s baseline=%s stutter=%s",
    rate, drops, dt, tostring(tier), tostring(baseline), tostring(stutter)))

  if tier == baseline then
    if stutter then
      bad_streak = bad_streak + 1
      if upgrade_pending then                    -- 回升试探期内复发 => 回滚+计失败
        fail_count = fail_count + 1
        upgrade_pending = nil
        if fail_count >= 2 then blocked_until = now + COOLDOWN_SECONDS end
        enter("emergency", "回升后复发")
      elseif bad_streak >= CONFIRM then
        enter("emergency", string.format("丢帧%.1f/s", rate))
      end
    else
      bad_streak = 0
      if upgrade_pending and now - upgrade_pending >= RECHECK_SECONDS then
        upgrade_pending, fail_count = nil, 0     -- 试探通过
      end
    end
  else                                           -- emergency: 够干净才试探回升
    if stutter then
      clean_accum = 0
    else
      clean_accum = clean_accum + dt
      if now >= blocked_until and clean_accum >= CLEAN_SECONDS then
        enter(baseline, "自动回升")
        upgrade_pending = now
      end
    end
  end
end)

mp.add_key_binding("ctrl+a", "toggle-auto-quality", function()
  auto = not auto
  mp.osd_message("自动画质档: " .. (auto and "开" or "关"), 2)
  publish()
end)

-- v5.4 手动覆盖：改托管属性前先关掉自动档，否则下一次换档会整组写回。
-- 返回 true 表示本次确实关掉了自动档（用于拼 OSD 提示）。
local function manual_override()
  if not auto then return false end
  auto = false
  publish()
  return true
end

mp.add_key_binding("ctrl+d", "toggle-deband-manual", function()
  local was_auto = manual_override()
  local on = mp.get_property_native("deband")
  set1("deband", on and "no" or "yes")
  mp.osd_message("deband: " .. (on and "关" or "开") ..
                 (was_auto and " ｜ 自动画质档已关" or ""), 3)
end)

mp.add_key_binding("ctrl+i", "toggle-interpolation-manual", function()
  local was_auto = manual_override()
  local on = mp.get_property_native("interpolation")
  set1("interpolation", on and "no" or "yes")
  mp.osd_message("interpolation: " .. (on and "关" or "开") ..
                 (was_auto and " ｜ 自动画质档已关" or ""), 3)
end)

-- 初始状态外露（脚本加载完就能被 IPC 读到）
publish()
