# -*- coding: utf-8 -*-
"""ui_web.py — 单窗口：内核看板本体 + 外壳控制层。

窗口里就是 `http://127.0.0.1:<port>/` 那个内核看板（账号管理、签到、任务、
代理出口、日志全在里面，一个功能都不缺），外壳只往页面注入一条控制层：

    [状态灯 运行中 · 端口 8788 · 账号 9/8 · 1.6.10]  [启动][停止][重启]
    [打开看板][复制 API 地址][复制 API Key][账号备注][检查更新][日志][退出]

三条设计约束：

1. **注入层是唯一被注入的东西**，且全部包在 try/catch 里。内核看板改版导致
   注入失败时，只是"控制层没出现"，看板与内核毫发无损。
2. 需要改看板外观时写 `data/dashboard.patch.js` / `.css`（外壳持有），
   **不编辑内核 dashboard.html**——这样内核更新不会覆盖你的改动。
3. 注入层不依赖看板的任何 DOM 结构：它只插入自己的 div，自己取数据。

**刻意不重复内核已有的功能**：复制 API 地址、复制 API Key、账号备注都由内核
看板自己提供（网关页的「复制」按钮、设置 → API Key 的「复制」、账号行的备注
按钮）。外壳只保留内核对等能力之外的那部分——进程启停、检查更新、退出。
少一份重复实现，就少一份"两处不一致"的可能。
"""

import json
import os
import re
import threading
import time

import config as config_mod
import logging_setup
import paths
import secret_store
import service as service_mod

WINDOW_TITLE = "WorkBuddy2API-Hub 网关"
BAR_ID = "__wb_shell_bar__"
DEFAULT_SIZE = (1180, 800)

# 控制层：一条底栏。颜色跟随看板的暗色风格，但不覆盖看板任何样式
# （所有规则都限定在 #__wb_shell_bar__ 内）。
CONTROL_BAR_JS = r"""
(function () {
  if (window.__wbShellBarLoaded) { return; }
  window.__wbShellBarLoaded = true;

  function ready(fn) {
    try {
      if (document.readyState === 'complete' || document.readyState === 'interactive') {
        setTimeout(fn, 0);
      } else {
        document.addEventListener('DOMContentLoaded', fn);
      }
    } catch (e) { /* 注入失败不影响看板 */ }
  }

  var STYLE = [
    '#' + '__wb_shell_bar__' + ' {',
    '  position: fixed; left: 0; right: 0; bottom: 0; z-index: 2147483000;',
    '  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;',
    '  padding: 6px 10px; font: 12px/1.5 "Microsoft YaHei UI", "Segoe UI", sans-serif;',
    '  background: rgba(24,26,30,.96); color: #d7dae0;',
    '  border-top: 1px solid #33363c; box-shadow: 0 -2px 10px rgba(0,0,0,.35);',
    '}',
    '#__wb_shell_bar__ button {',
    '  font: inherit; color: #d7dae0; background: #2b2f36; border: 1px solid #3d424a;',
    '  border-radius: 4px; padding: 3px 9px; cursor: pointer; }',
    '#__wb_shell_bar__ button:hover { background: #363b44; }',
    '#__wb_shell_bar__ button:disabled { opacity: .45; cursor: default; }',
    '#__wb_shell_bar__ .wb-toast { color: #7bd88f; margin-left: 6px; }',
    '#__wb_shell_bar__ .wb-toast.bad { color: #ff8787; }',
    /* 状态区：左键/右键都开菜单。
       左键刻意不执行任何动作——只弹菜单，所以不会因为误触把内核停掉或重启。 */
    '#__wb_shell_bar__ .wb-status {',
    '  display: inline-flex; align-items: center; gap: 7px; cursor: pointer;',
    '  padding: 3px 10px 3px 8px; border-radius: 5px; border: 1px solid transparent;',
    '  user-select: none; }',
    '#__wb_shell_bar__ .wb-status:hover { background: #2b2f36; border-color: #3d424a; }',
    '#__wb_shell_bar__ .wb-status .wb-caret { color: #9aa0a6; font-size: 11px; }',
    '#__wb_shell_bar__ .wb-dot { width: 9px; height: 9px; border-radius: 50%;',
    '  background: #8a8f98; flex: 0 0 auto; }',
    '#__wb_shell_bar__ .wb-label { font-weight: 600; color: #d7dae0; }',
    '#__wb_shell_bar__ .wb-info { color: #9aa0a6; }',
    '#__wb_shell_bar__ .wb-update { color: #ffd166; font-weight: 600; cursor: pointer; }',
    '#__wb_shell_bar__ .wb-update:hover { text-decoration: underline; }',
    /* 二级菜单 */
    '#__wb_shell_menu__ { position: fixed; z-index: 2147483002; min-width: 208px;',
    '  background: #22242a; border: 1px solid #3d424a; border-radius: 7px;',
    '  box-shadow: 0 8px 26px rgba(0,0,0,.5); padding: 5px;',
    '  font: 12px/1.5 "Microsoft YaHei UI", "Segoe UI", sans-serif; color: #d7dae0; }',
    '#__wb_shell_menu__ .wb-mi {',
    '  display: flex; align-items: center; gap: 8px; padding: 6px 9px;',
    '  border-radius: 5px; cursor: pointer; white-space: nowrap; }',
    '#__wb_shell_menu__ .wb-mi:hover { background: #2f333b; }',
    '#__wb_shell_menu__ .wb-mi.off { opacity: .4; cursor: default; }',
    '#__wb_shell_menu__ .wb-mi.off:hover { background: transparent; }',
    '#__wb_shell_menu__ .wb-mi .wb-key { margin-left: auto; color: #8a8f98;',
    '  font-size: 11px; }',
    '#__wb_shell_menu__ .wb-sep { height: 1px; background: #3d424a; margin: 4px 6px; }',
    '#__wb_shell_menu__ .wb-head { padding: 5px 9px 7px; color: #9aa0a6;',
    '  border-bottom: 1px solid #3d424a; margin-bottom: 4px; }',
    /* 内核位置对话框 / 通用确认框 / 更新对话框（共用一套样式类） */
    '#__wb_settings__, #__wb_confirm__, #__wb_update__ { position: fixed; inset: 0;',
    '  z-index: 2147483003; background: rgba(0,0,0,.5); display: flex;',
    '  align-items: center; justify-content: center;',
    '  font: 13px/1.6 "Microsoft YaHei UI", "Segoe UI", sans-serif; }',
    '#__wb_settings__ .box, #__wb_confirm__ .box, #__wb_update__ .box {',
    '  background: #1e1f22; color: #d7dae0;',
    '  width: min(700px, 92vw); max-height: 86vh; overflow: auto;',
    '  border: 1px solid #3d424a; border-radius: 8px; padding: 16px; }',
    '#__wb_settings__ h3, #__wb_confirm__ h3, #__wb_update__ h3 {',
    '  margin: 0 0 10px; font-size: 14px; }',
    '#__wb_settings__ .kv, #__wb_confirm__ .kv, #__wb_update__ .kv {',
    '  margin: 3px 0; color: #9aa0a6; }',
    '#__wb_settings__ .kv b, #__wb_confirm__ .kv b, #__wb_update__ .kv b {',
    '  color: #d7dae0; font-weight: 600; }',
    '#__wb_settings__ .mono, #__wb_confirm__ .mono, #__wb_update__ .mono {',
    '  font-family: Consolas, monospace; font-size: 12px; }',
    '#__wb_settings__ input { width: 100%; box-sizing: border-box; margin: 8px 0 4px;',
    '  background: #16171a; color: #d7dae0; border: 1px solid #3d424a;',
    '  border-radius: 5px; padding: 7px 9px; font: 12px Consolas, monospace; }',
    '#__wb_settings__ .cand { margin: 4px 0; padding: 5px 8px; border-radius: 5px;',
    '  background: #23262c; cursor: pointer; font-family: Consolas, monospace;',
    '  font-size: 12px; display: flex; gap: 8px; align-items: center; }',
    '#__wb_settings__ .cand:hover { background: #2c3038; }',
    '#__wb_settings__ .cand.bad { opacity: .6; cursor: default; }',
    '#__wb_settings__ .tag { font-family: inherit; font-size: 11px; color: #9aa0a6; }',
    '#__wb_settings__ .msg { margin: 6px 0; min-height: 20px; }',
    '#__wb_settings__ .msg.ok { color: #7bd88f; }',
    '#__wb_settings__ .msg.bad { color: #ff8787; }',
    '#__wb_settings__ .warn, #__wb_confirm__ .warn, #__wb_update__ .warn {',
    '  color: #d29b00; }',
    '#__wb_update__ .bad { color: #ff8787; }',
    /* 设置对话框专用：小节标题 + 勾选行（复选框不能被 input 的 100% 宽吃掉） */
    '#__wb_settings__ h4 { margin: 14px 0 6px; font-size: 13px; color: #d7dae0;',
    '  border-left: 3px solid #2f6feb; padding-left: 8px; }',
    '#__wb_settings__ .chk { display: flex; align-items: center; gap: 8px;',
    '  margin: 10px 0 4px; color: #d7dae0; cursor: pointer; }',
    '#__wb_settings__ .chk input { width: auto; margin: 0; flex: 0 0 auto; }',
    /* 更新对话框专用：进度条 + 步骤/更新说明的滚动框（等宽、保留换行） */
    '#__wb_update__ .bar { height: 8px; border-radius: 4px; background: #16171a;',
    '  border: 1px solid #3d424a; margin: 10px 0 2px; overflow: hidden; }',
    '#__wb_update__ .bar > i { display: block; height: 100%; width: 0;',
    '  background: #2f6feb; transition: width .2s linear; }',
    '#__wb_update__ .log { white-space: pre-wrap; margin-top: 4px;',
    '  font-family: Consolas, monospace; font-size: 12px; color: #9aa0a6;',
    '  background: #16171a; border: 1px solid #3d424a; border-radius: 5px;',
    '  padding: 8px 10px; max-height: 30vh; overflow: auto; }',
    '#__wb_settings__ .row, #__wb_confirm__ .row, #__wb_update__ .row {',
    '  display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }',
    '#__wb_settings__ button, #__wb_confirm__ button, #__wb_update__ button {',
    '  font: inherit;',
    '  background: #2b2f36; color: #d7dae0; border: 1px solid #3d424a;',
    '  border-radius: 5px; padding: 5px 14px; cursor: pointer; }',
    '#__wb_settings__ button.primary, #__wb_confirm__ button.primary,',
    '#__wb_update__ button.primary {',
    '  background: #2f6feb; border-color: #2f6feb; }',
    '#__wb_settings__ button:disabled { opacity: .45; cursor: default; }',
    '#__wb_settings__ hr, #__wb_update__ hr { border: none;',
    '  border-top: 1px solid #3d424a;',
    '  margin: 14px 0 10px; }',
    /* 设置页会越写越长（内核位置 + 面板密码 + 局域网共享）：改成"标题栏与底部
       按钮固定、中间那段自己滚"的经典布局——**关闭按钮永远在视野里**，
       不用再滚到底去找它（底部那句提示：Esc / 点对话框外面也能关）。 */
    '#__wb_settings__ .box { display: flex; flex-direction: column;',
    '  padding: 0; overflow: hidden; }',
    /* 固定高度（2026-10-18 用户反馈）：以前只有 max-height，内容少时对话框就缩，
       展开/收起某一节会让**整个对话框忽高忽低**，看着很难受。
       现在给一个确定的高度，让内部的 .body 自己滚——盒子尺寸恒定，
       折叠只影响内部滚动，视觉稳定。 */
    '#__wb_settings__ .box { height: min(86vh, 720px); }',
    '#__wb_settings__ .head { display: flex; align-items: center; gap: 10px;',
    '  padding: 14px 16px 10px; border-bottom: 1px solid #3d424a; }',
    '#__wb_settings__ .head h3 { margin: 0; flex: 1 1 auto; }',
    '#__wb_settings__ .head .x { font-size: 16px; line-height: 1;',
    '  padding: 3px 10px; }',
    '#__wb_settings__ .body { flex: 1 1 auto; min-height: 0; overflow: auto;',
    '  padding: 14px 16px; }',
    /* 滚到底/滚到顶时**别把滚动漏给后面的看板**（那会让观感变成"滑着滑着就滑不动了"，
       甚至跟看板自己的滚轮逻辑打架——用户实测报过）。 */
    '#__wb_settings__ .body { overscroll-behavior: contain; }',
    '#__wb_settings__ .foot { display: flex; align-items: center; gap: 8px;',
    '  padding: 10px 16px 14px; border-top: 1px solid #3d424a;',
    '  background: #1e1f22; }',
    '#__wb_settings__ .foot .hint { margin-right: auto; color: #9aa0a6;',
    '  font-size: 12px; }',
    /* 可折叠分节（2026-10-18）：设置项变多之后，一屏看不全、翻到下面很烦。
       每节变成"点标题展开/收起"的手风琴；标题行本身就是整行的按钮，
       点击热区大、不用瞄准小三角。 */
    '#__wb_settings__ .sec-head { display: flex; align-items: center; gap: 8px;',
    '  cursor: pointer; user-select: none; padding: 7px 4px; margin: 2px 0;',
    '  border-radius: 5px; }',
    '#__wb_settings__ .sec-head:hover { background: #2b2f36; }',
    '#__wb_settings__ .sec-head h4 { margin: 0; flex: 1 1 auto; font-size: 13px;',
    '  color: #d7dae0; }',
    '#__wb_settings__ .sec-head .sec-caret { color: #9aa0a6; font-size: 11px;',
    '  width: 12px; text-align: center; }',
    '#__wb_settings__ .sec-head .sec-badge { color: #9aa0a6; font-size: 11px; }',
    '#__wb_settings__ .sec-body { padding: 2px 4px 10px; }',
    /* 收起时连 <hr> 一起藏掉，否则只剩一排横线看着像分隔错乱 */
    '#__wb_settings__ .sec.collapsed .sec-body { display: none; }',
    '#__wb_settings__ .sec.collapsed .sec-head { border-bottom: 1px solid #2f333b; }',
    /* 局域网共享那一节：地址与 Key 用等宽显示，长串换行而不是撑破对话框 */
    '#__wb_settings__ .kv.ok { color: #7bd88f; }',
    '#__wb_settings__ .lan-row { margin: 6px 0; padding: 6px 8px;',
    '  background: #23262c; border-radius: 5px;',
    '  font-family: Consolas, monospace; font-size: 12px;',
    '  word-break: break-all; }',
    '#__wb_settings__ .lan-row b { color: #d7dae0; }',
    /* 地址行：地址在左、复制按钮在右；带 Key 那句代价说明独占一行 */
    '#__wb_settings__ .lan-line { display: flex; align-items: center; gap: 8px;',
    '  flex-wrap: wrap; margin: 5px 0; }',
    '#__wb_settings__ .lan-line .mono { word-break: break-all; }',
    '#__wb_settings__ .lan-line button { flex: 0 0 auto; }',
    '#__wb_settings__ .lan-line .tag { flex: 1 1 100%; }',
    '#__wb_settings__ .keybox { display: flex; gap: 8px; align-items: center;',
    '  margin: 6px 0; }',
    '#__wb_settings__ .keybox input { margin: 0; flex: 1 1 auto; }',
    '#__wb_settings__ .keybox button { flex: 0 0 auto; }'
  ].join('\n');

  var COLORS = { running: '#2f9e44', starting: '#d29b00', stopping: '#d29b00',
                 stopped: '#8a8f98', failed: '#e03131', external: '#1c7ed6' };

  var MENU_ID = '__wb_shell_menu__';
  var state = {};
  // 开合轨迹（最多留 20 条）：菜单"自己又开了"这类问题靠它才查得清，
  // 也方便用 DOM 探针复盘时序。读 window.__wbShellMenuTrace（数组）。
  var trace = [];
  window.__wbShellMenuTrace = trace;

  function note(text) {
    try {
      trace.push(text);
      if (trace.length > 20) { trace.shift(); }
    } catch (e) { /* 忽略 */ }
  }

  function call(name, payload) {
    try {
      if (!window.pywebview || !window.pywebview.api || !window.pywebview.api[name]) {
        return Promise.reject(new Error('api 尚未就绪'));
      }
      return Promise.resolve(window.pywebview.api[name](payload || {}));
    } catch (e) { return Promise.reject(e); }
  }

  function toast(text, bad) {
    try {
      var el = document.getElementById('__wb_toast__');
      if (!el) { return; }
      el.textContent = text || '';
      el.className = 'wb-toast' + (bad ? ' bad' : '');
      if (text) { setTimeout(function () { el.textContent = ''; }, 4000); }
    } catch (e) { /* 忽略 */ }
  }

  function run(name) {
    var busy = { start: 1, stop: 1, restart: 1 };
    if (busy[name]) { toast('正在执行…'); }
    if (name === 'open') { call('open_dashboard'); return; }
    if (name === 'quit') { call('quit'); return; }
    if (name === 'settings') { settingsDialog(); return; }
    if (name === 'update') { updateFlow(); return; }
    call('action', { name: name }).then(function (res) {
      var out = typeof res === 'string' ? JSON.parse(res) : res;
      if (out && out.message) { toast(out.message, !out.ok); }
    }).catch(function (err) { toast('执行失败：' + err.message, true); });
  }

  // ---- 复制：三条路，最后一条是外壳自己写剪贴板 --------------------------
  //
  // 为什么不能只靠浏览器那两条（2026-10-15 用户报障"无法复制"）：
  //   * `navigator.clipboard.writeText` 在 WebView2 里可能被权限拦下；
  //   * 而"等它 reject 之后再退回 `document.execCommand('copy')`"是错的——
  //     execCommand 同样要求**用户手势**，Promise 被拒时手势早就过期了。
  //     两条一起失败，用户看到的就是"点了没反应"。
  // 所以顺序改成：**同步**的 execCommand 先试（此时手势还在）→ 再试异步
  // clipboard API → 都不行就交给外壳（Win32 写剪贴板，不看浏览器脸色）。
  function copyText(text, onDone) {
    function done(ok, why) { if (onDone) { onDone(ok, why || ''); } }
    if (!text) { done(false, '没有要复制的内容'); return; }
    if (fallbackCopy(text)) { done(true, ''); return; }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { done(true, ''); },
          function () { copyViaShell(text, done); });
        return;
      }
    } catch (e) { /* 落到外壳那条路 */ }
    copyViaShell(text, done);
  }

  function copyViaShell(text, done) {
    call('copy', { text: text }).then(function (res) {
      var out = norm(res) || {};
      done(!!out.ok, out.message || '');
    }).catch(function (err) {
      done(false, (err && err.message) || '外壳没有回应');
    });
  }

  function fallbackCopy(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return !!ok;
    } catch (e) { return false; }
  }

  // 设置页的关闭入口有四个：右上角 ×、底部「关闭」、Esc、点对话框外面。
  // 四个都走这一个函数，行为不会两样（内容多了以后，关闭不该只有一条路，
  // 也不该逼人滚到底——见 STYLE 里 .head/.foot 那两段注释）。
  function closeSettingsDialog() {
    var existed = false;
    try {
      var el = document.getElementById('__wb_settings__');
      existed = !!el;
      if (el) { el.remove(); }
    } catch (e) { /* 忽略 */ }
    // 关上就把"锁住后面那层看板"的锁摘掉（幂等：没锁也不出错）
    lockBackgroundScroll(false);
    return existed;
  }

  // ---- 对话框开着时的滚动隔离 ---------------------------------------------
  //
  // 用户实测：在设置页里**来回滑几次之后滚动就失效了**——看板自己有滚轮/滚动
  // 逻辑，跟对话框的 .body 抢。两条一起用：
  //   1) .body 上 `overscroll-behavior: contain`（见 STYLE）：滚到底不再把
  //      滚动"接力"给后面的看板；
  //   2) 对话框开着时，在 window 的**捕获阶段**截住滚轮事件，让它传不到看板
  //      注册在 document/window 上的处理函数。浏览器对 .body 的滚动是**默认
  //      动作**、不靠监听器，所以对话框内部照常滚。
  var scrollLock = null;

  function blockWheelWhileOpen(ev) {
    try {
      if (ev.target && ev.target.closest &&
          ev.target.closest('#__wb_settings__')) {
        ev.stopPropagation();     // 只截"发生在对话框里"的滚轮
      }
    } catch (e) { /* 忽略 */ }
  }

  function lockBackgroundScroll(on) {
    try {
      if (on) {
        if (scrollLock) { return; }
        scrollLock = {html: document.documentElement.style.overflow,
                      body: document.body.style.overflow};
        document.documentElement.style.overflow = 'hidden';
        document.body.style.overflow = 'hidden';
        window.addEventListener('wheel', blockWheelWhileOpen, true);
        return;
      }
      if (!scrollLock) { return; }
      document.documentElement.style.overflow = scrollLock.html || '';
      document.body.style.overflow = scrollLock.body || '';
      scrollLock = null;
      window.removeEventListener('wheel', blockWheelWhileOpen, true);
    } catch (e) { /* 锁不上也不能挡着用 */ }
  }

  // Key 打码：只留头尾，够认出"是哪一个"就行
  function maskKey(key) {
    key = String(key || '');
    if (!key) { return ''; }
    if (key.length <= 12) { return key.slice(0, 2) + '…' + key.slice(-2); }
    return key.slice(0, 6) + '…' + key.slice(-4);
  }

  // ---- 设置：内核位置 + 面板密码 -----------------------------------------
  //
  // 「内核位置」原来在菜单里是独立一项，现在收进设置——两件事都是"配一次、
  // 平时不动"的，放一起菜单也短了。
  //
  // 内核位置解决不了"启动阶段就找不到内核"那种情况：那时没有底栏、没有菜单，
  // 只能靠启动参数：启动.vbs --core-dir <路径>。
  function settingsDialog() {
    try {
      call('kernel_info').then(function (res) {
        var info = norm(res) || {};
        // 设置窗口**先开出来**，再异步去填「局域网共享」那一节。
        //
        // 为什么不让它等 lan_info：pywebview 的回调表是**按文档**的
        // （`window.pywebview._returnValuesCallbacks`），页面一旦被替换
        // （导航/重载，WebView2 的错误页也会自己重试），在途的那次调用就永远
        // 不会 resolve——实测踩到过：设置窗口卡在"打不开"。
        // 一个"可选的附加信息"绝不该拖住整个设置界面。
        call('panel_status').then(function (pres) {
          // 内核守护设置：本地读配置，很快；一起拿来再开对话框。
          call('watchdog_get').then(function (wres) {
            showSettingsDialog(info, norm(pres) || {}, null, norm(wres) || {});
          }).catch(function () {
            showSettingsDialog(info, norm(pres) || {}, null);
          });
        }).catch(function () {
          showSettingsDialog(info, {}, null);
        });
      }).catch(function (err) {
        toast('读取设置失败：' + ((err && err.message) || err), true);
      });
    } catch (e) { toast('打开设置失败', true); }
  }

  // 给 js_api 调用加超时：桥那头的回调可能永远不回来（见 settingsDialog 的注释），
  // 卡住时界面要能自己说清楚，而不是一直转圈。
  function withTimeout(promise, ms, label) {
    return new Promise(function (resolve, reject) {
      var settled = false;
      var timer = setTimeout(function () {
        if (!settled) {
          settled = true;
          reject(new Error((label || '外壳') + '没有在 ' + Math.round(ms / 1000)
                           + ' 秒内回应'));
        }
      }, ms);
      Promise.resolve(promise).then(function (value) {
        if (!settled) { settled = true; clearTimeout(timer); resolve(value); }
      }, function (err) {
        if (!settled) { settled = true; clearTimeout(timer); reject(err); }
      });
    });
  }

  // ---- 局域网共享（LAN 模式）-----------------------------------------------
  //
  // 内核侧早就实现了（`--lan` → `--host 0.0.0.0` + 强制 API Key，Key 存
  // accounts\settings.json），外壳也早就把 `--lan` 传给内核了——缺的只是**界面**：
  // 以前要开 LAN 只能手编 shell.json，而且 Key 只印在内核启动 banner 里，
  // 被外壳抓进 data\core-<port>.log（README §九「想看内核原始输出」那条）。
  // 这一节就是把那点信息搬到界面上，外加一个开关。
  //
  // 这一节**不碰内核**：Key 只从 accounts\settings.json 里读（只读）。
  function lanHtml(lan) {
    lan = lan || {};
    var on = !!lan.config_lan;
    var live = lan.live_lan;
    var keys = lan.key || {};
    var accepted = keys.accepted || [];
    var primary = (accepted[0] || {}).key || '';
    // 地址行：**默认不带 Key**。凭据要不要带，是用户的选择，不是外壳替他定的
    // （2026-10-15 用户反馈："复制带 key 为什么会默认带一个 key，没有问过我的选择"）。
    // 所以 API 地址与看板地址各有自己的「复制」，都不含 key；带 Key 的直链单独一个
    // 按钮、标签里写明代价，而且屏上只显示 `?key=…`（原文只放在 data-copy 里）。
    var rows = (lan.links || []).map(function (item, i) {
      return '<div class="lan-row">[' + (i + 1) + '] <b>' + esc(item.ip) + '</b>' +
        '<div class="lan-line"><span class="mono">API 地址：' + esc(item.api) +
          '</span><button data-act="copy" data-copy="' + esc(item.api) +
          '">复制 API 地址</button></div>' +
        '<div class="lan-line"><span class="mono">看板地址：' + esc(item.dashboard) +
          '</span><button data-act="copy" data-copy="' + esc(item.dashboard) +
          '">复制看板地址</button></div>' +
        (primary
          ? '<div class="lan-line"><span class="mono">带 Key 的看板直链：' +
            esc(item.dashboard) + '?key=…</span>' +
            '<button data-act="copy" data-copy="' + esc(item.dashboard_keyed) +
            '">复制带 Key 的直链</button>' +
            '<span class="tag">只有点这个才带上 Key。它会替你填好看板里的 Key，' +
            '代价是凭据会进剪贴板、也可能进浏览器历史——不想留痕就用上面两个。</span>' +
            '</div>'
          : '') +
        '</div>';
    }).join('') || ('<div class="lan-row">没有找到内网 IPv4 地址' +
                     '（这台机器可能只有回环网卡）。</div>');

    var liveText = live === true ? '是：正在监听 0.0.0.0（局域网里能连）'
                 : (live === false ? '否：只监听 127.0.0.1（别的设备连不上）'
                                   : '未知：这个内核不是本外壳启动的，判断不了');

    // Key 这一节必须按内核**真实的**规矩讲：面板里一旦有启用的 Key，
    // 就只有那些 Key 被接受（--api-key 与自动生成的 launcher_key 都作废）。
    // 照旧文档那样只显示 launcher_key，会让人去填一个根本不认的值。
    //
    // 显示上跟随内核看板自己的习惯（settings.json 里有 key_before_hidden）：
    // **默认打码**，要看/要复制都得自己点一下——截图或投屏不会顺手泄露 Key。
    var keyHtml;
    if (accepted.length) {
      keyHtml = accepted.map(function (item, i) {
        var label = item.label || ('Key ' + (i + 1));
        if (item.realm) { label += '（realm: ' + item.realm + '）'; }
        return '<div class="kv">' + esc(label) + '：</div>' +
          '<div class="keybox"><input id="__wb_lan_key_' + i +
            '__" readonly value="' + esc(maskKey(item.key)) +
            '" data-real="' + esc(item.key) + '" data-masked="1">' +
          '<button data-act="reveal">显示</button>' +
          '<button data-act="copy" data-copy="' + esc(item.key) +
            '">复制</button></div>';
      }).join('');
    } else {
      keyHtml = '<div class="kv warn">没有可用的 Key' +
        (keys.auth_disabled ? '（内核关掉了 Key 校验，不需要）' : '') + '。</div>';
    }
    var others = (keys.others || []).map(function (text) {
      return '<div class="kv warn">' + esc(text) + '</div>';
    }).join('');

    return '' +
      '<div class="kv">运行中的内核：<b>' + esc(liveText) + '</b></div>' +
      '<div class="kv">外壳配置：<b>' + (on ? '开启' : '关闭') + '</b>' +
        (lan.needs_restart
          ? '　<span class="warn">改了配置但还没重启内核，尚未生效</span>' : '') +
      '</div>' +
      (lan.cli_override
        ? '<div class="kv warn">启动参数里带了 --lan（或 bootstrap.json 提供了它），' +
          '它优先——在这里改配置不会生效。</div>' : '') +
      (lan.core_external
        ? '<div class="kv warn">这个内核是别处启动的：外壳能读它、但不能替你重启。' +
          '开关会写进配置，重启内核请在你启动它的那个窗口里做。</div>' : '') +
      '<label class="chk"><input type="checkbox" id="__wb_lan_toggle__"' +
        (on ? ' checked' : '') + '> 允许局域网内其它设备访问（LAN 模式）' +
        '——开启后内核会强制要求 API Key</label>' +
      '<div class="kv">本机内网地址（别的设备用这些）。<b>默认不带 Key</b>：' +
        'API 地址与看板地址都能一键复制，两个都不含凭据。</div>' + rows +
      '<div class="kv">别的设备要填的 API Key（来源：<b>' +
        esc(keys.source || '未知') + '</b>）：</div>' + keyHtml +
      (keys.detail ? '<div class="kv"><span class="tag">' + esc(keys.detail) +
                     '</span></div>' : '') +
      others +
      (lan.reachable
        ? '<div class="kv ok">连通性自检：外壳刚试着从 ' + esc(lan.reachable) +
          ' 问了一次 /health，有应答（局域网里连得上）。</div>'
        : (lan.checked_reach
            ? '<div class="kv warn">连通性自检：从内网地址问 /health 没有应答' +
              '（可能还没开 LAN，或者被防火墙拦了）。</div>' : '')) +
      '<div class="kv warn">风险：谁拿到这个 Key，谁就能花你账号的额度。</div>' +
      '<div class="kv warn">不要把 8788 映射到公网：LAN 模式是给同一个局域网用的，' +
        '暴露到公网等于把额度交给互联网。</div>' +
      '<div class="kv">' + esc(lan.firewall || '') + '</div>' +
      '<div class="msg" id="__wb_lan_msg__"></div>' +
      '<div class="row">' +
        '<button data-act="lan-save">只保存（稍后自己重启内核）</button>' +
        '<button class="primary" data-act="lan-save-restart">保存并重启内核</button>' +
      '</div>';
  }

  // ---- 设置页的可折叠分节（手风琴）----------------------------------------
  //
  // 需求原话："设置页越来越多设置项，翻到下面很麻烦。" 做法：每个 <h4> 连同它
  // 后面的兄弟节点收进一个 .sec；点标题行展开/收起。
  //
  // 两条纪律（都是踩过的坑的镜像）：
  //  1. **收起的节只是 display:none，节点还在 DOM 里。** 所以 `#__wb_lan_box__`
  //     这类异步填充的容器照旧能被 fillLanBox() 找到并更新，不会因为"没展开"
  //     就填不进去（局域网那节是异步来的，这一点尤其重要）。
  //  2. **它一行后端代码都不碰**：不调 api_*、不改保存逻辑、不动 .head/.foot。
  //     抛异常时 buildCollapsibleSections 被 try 包住，最坏结果只是"不折叠"。

  var SECTIONS_KEY = '__wb_shell_settings_open__';

  function readOpenSection() {
    try {
      var value = window.localStorage.getItem(SECTIONS_KEY);
      return value === null ? null : String(value);
    } catch (e) { return null; }   // 隐私模式/存储被禁：退回默认
  }

  function writeOpenSection(id) {
    try { window.localStorage.setItem(SECTIONS_KEY, String(id || '')); }
    catch (e) { /* 记不住就算了，不影响折叠本身 */ }
  }

  function buildCollapsibleSections(wrap) {
    var body = wrap.querySelector('.body');
    if (!body) { return; }
    var heads = Array.prototype.slice.call(body.querySelectorAll('h4'));
    if (!heads.length) { return; }

    // 记住"打开设置时该展开哪一节"：上次看的那节；没有记录就展开第一节。
    var wanted = readOpenSection();
    var sections = [];

    for (var i = 0; i < heads.length; i++) {
      var h4 = heads[i];
      var sec = document.createElement('div');
      sec.className = 'sec';
      // 稳定的 id：用标题文本（中文标题唯一、且顺序可能变），去掉空格与括号里的补充说明
      var title = (h4.textContent || '').trim();
      sec.setAttribute('data-sec', title);
      if (!wanted && i === 0) { wanted = title; }   // 默认展开第一节

      // 标题行：整行可点
      var head = document.createElement('div');
      head.className = 'sec-head';
      head.setAttribute('role', 'button');
      head.setAttribute('tabindex', '0');
      head.setAttribute('title', '展开 / 收起这一节');
      var caret = document.createElement('span');
      caret.className = 'sec-caret';
      var label = document.createElement('h4');
      label.textContent = title;
      head.appendChild(caret);
      head.appendChild(label);

      var secBody = document.createElement('div');
      secBody.className = 'sec-body';

      // 把 h4 之后、下一个 h4 之前的兄弟节点（含分隔用的 <hr>）搬进 secBody
      var node = h4.nextSibling;
      var collected = [];
      while (node) {
        var next = node.nextSibling;
        if (node.nodeType === 1 && node.tagName === 'H4') { break; }
        collected.push(node);
        node = next;
      }

      // 组装：sec 替换掉原来的 h4 位置
      h4.parentNode.insertBefore(sec, h4);
      sec.appendChild(head);
      sec.appendChild(secBody);
      h4.parentNode.removeChild(h4);
      for (var k = 0; k < collected.length; k++) {
        secBody.appendChild(collected[k]);
      }
      sections.push({ el: sec, caret: caret, title: title });

      (function (item) {
        function toggle() {
          var collapsed = item.el.classList.toggle('collapsed');
          item.caret.textContent = collapsed ? '▸' : '▾';
          if (!collapsed) {
            // 一次只开一节：点开的那节排他展开，避免又回到"全展开要滚"
            for (var j = 0; j < sections.length; j++) {
              if (sections[j] !== item) {
                sections[j].el.classList.add('collapsed');
                sections[j].caret.textContent = '▸';
              }
            }
            writeOpenSection(item.title);
          } else if (readOpenSection() === item.title) {
            writeOpenSection('');
          }
        }
        item.el.__wbToggle = toggle;
        head.addEventListener('click', function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          toggle();
        });
        head.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            toggle();
          }
        });
      }(sections[sections.length - 1]));
    }

    // 应用初始状态：只展开 wanted（上次那节 / 第一节）
    var opened = false;
    for (var m = 0; m < sections.length; m++) {
      var shouldOpen = !opened && sections[m].title === wanted;
      if (shouldOpen) { opened = true; }
      sections[m].el.classList.toggle('collapsed', !shouldOpen);
      sections[m].caret.textContent = shouldOpen ? '▾' : '▸';
    }
    if (!opened && sections.length) {
      // 记录的标题已经不在了（改了名字）：退回展开第一节
      sections[0].el.classList.remove('collapsed');
      sections[0].caret.textContent = '▾';
    }

    // 滚动位置归零。为什么必须显式做：设置页常在上一轮被滚到过中间，
    // 而 `body` 的 scrollTop 会保留；改造后第一节标题正好贴顶，
    // 于是被固定的 `.head` 压住半行（实拍截图里就是这样）。
    try {
      body.scrollTop = 0;
      // 逐个 section 也清一遍（有些 WebView2 版本会把滚动挂到子元素上）
      for (var n = 0; n < sections.length; n++) { sections[n].el.scrollTop = 0; }
    } catch (e) { /* 清不掉也不影响功能 */ }
  }

  function showSettingsDialog(info, panel, lan, wd) {
    // wd = 内核守护设置；取不到也给一份默认值，别让整个对话框打不开
    wd = wd || { enabled: true, retries: 3, note: '' };
    try {
      var old = document.getElementById('__wb_settings__');
      if (old) { old.remove(); }

      var warns = [];
      if (info.override) {
        warns.push('当前用的是启动参数 --core-dir 指定的目录；' +
                   '在这里保存会被写进 bootstrap.json，下次启动时它优先。');
      }
      var cands = (info.candidates || []).map(function (c, i) {
        var cls = 'cand' + (c.valid ? '' : ' bad');
        var state = c.valid ? '可用' : '不是内核目录';
        if (info.core_dir && c.path === info.core_dir) { state += ' · 当前使用'; }
        return '<div class="' + cls + '" data-path="' + esc(c.path) + '"' +
               (c.valid ? '' : ' title="' + esc(state) + '"') + '>' +
               '<span>[' + (i + 1) + '] ' + esc(c.path) + '</span>' +
               '<span class="tag">' + esc(c.note || '') + ' · ' +
               esc(state) + '</span></div>';
      }).join('');

      var need = panel.required === false ? '不需要（内核没设密码）'
               : (panel.required === true ? '需要' : '未知');
      var panelHtml =
        '<div class="kv">面板是否需要密码：<b>' + need + '</b>' +
          (panel.is_default
            ? '　<span class="warn">内核当前用的是默认密码 admin</span>' : '') +
        '</div>' +
        '<div class="kv">已记住密码：<b>' +
          (panel.have_secret ? '是' : '否') + '</b>' +
          (panel.have_secret && panel.saved_at
            ? '（' + esc(panel.saved_at) + '）' : '') + '</div>' +
        '<label class="chk"><input type="checkbox" id="__wb_remember__"' +
          (panel.remember ? ' checked' : '') +
          '> 记住面板密码（重开窗口 / 重启内核后自动登录）</label>' +
        '<div class="kv">密码用 Windows DPAPI 按<b>当前用户</b>加密存放' +
          '（<span class="mono">data\\panel-password.sealed</span>），不进日志；' +
          '换个 Windows 用户或换台机器都解不开。勾上之后第一次仍需你手输一次，' +
          '外壳会把它记下来。</div>' +
        '<div class="kv warn">代价说清：勾上之后，"能打开这个窗口的人"就等于' +
          '能进面板（本机这道门不再拦人）。</div>' +
        '<div class="msg" id="__wb_panel_msg__"></div>' +
        '<div class="row"><button data-act="forget">忘记已记住的密码</button></div>';

      var wrap = document.createElement('div');
      wrap.id = '__wb_settings__';
      wrap.className = '__wb_modal__';
      // 三节：内核位置 / 面板密码 / 局域网共享。
      // 结构是 .head（固定）+ .body（自己滚）+ .foot（固定）——见 STYLE 里的注释：
      // 关闭按钮永远在视野里，不用滚到底去找。
      wrap.innerHTML =
        '<div class="box" style="width:min(760px,94vw)">' +
          '<div class="head">' +
            '<h3>设置</h3>' +
            '<button class="x" data-act="cancel"' +
              ' title="关闭（Esc，或点对话框外面）">×</button>' +
          '</div>' +
          '<div class="body">' +
            '<h4>内核位置</h4>' +
            '<div class="kv">外壳目录：<b class="mono">' + esc(info.shell_dir || '') + '</b></div>' +
            '<div class="kv">当前内核：<b class="mono">' + esc(info.core_dir || '（未找到）') + '</b>' +
              (info.version ? ' · 版本 ' + esc(info.version) : '') + '</div>' +
            '<div class="kv">认来的途径：<b>' + esc(info.source || '未知') + '</b>' +
              ' · ' + (info.valid ? '目录完整'
                       : '<span class="warn">' + esc(info.problem || '不完整') + '</span>') +
            '</div>' +
            '<div class="kv">记忆文件：<b class="mono">' + esc(info.bootstrap || '') + '</b>' +
              (info.has_bootstrap ? '（已存在）' : '（尚未创建）') + '</div>' +
            warns.map(function (w) {
              return '<div class="kv warn">提示：' + esc(w) + '</div>';
            }).join('') +
            '<div style="margin-top:8px">把内核位置钉下来' +
              '（以后搬动或同级有多个候选时，外壳不会猜错）：</div>' +
            '<input id="__wb_kdir_input__" spellcheck="false" placeholder="' +
              '例如 D:\\\\somewhere\\\\wb-proxy" value="' + esc(info.core_dir || '') + '">' +
            '<div class="msg" id="__wb_kdir_msg__"></div>' +
            (cands ? '<div class="kv" style="margin-top:8px">自动发现的候选' +
                     '（点一下填进输入框）：</div>' + cands : '') +
            '<div class="row">' +
              '<button data-act="validate">校验</button>' +
              '<button class="primary" data-act="save">保存并重启外壳</button>' +
            '</div>' +
            '<hr>' +
            '<h4>面板密码</h4>' + panelHtml +
            '<hr>' +
            '<h4>内核守护</h4>' +
            '<div class="kv">内核意外退出时自动重新拉起，并弹一次托盘提醒。' +
              '连续失败会退避（5 秒 → 15 秒 → 60 秒），用完次数后停手等你处理。</div>' +
            '<label class="kv"><input type="checkbox" id="__wb_wd_enabled__"' +
              (wd.enabled ? ' checked' : '') + '> 开启内核守护</label>' +
            '<div class="kv">最多自动重启 ' +
              '<input id="__wb_wd_retries__" type="number" min="0" max="10"' +
              ' value="' + esc(String(wd.retries)) + '"' +
              ' style="width:64px"> 次（0 = 只提醒不重启）</div>' +
            '<div class="msg" id="__wb_wd_msg__"></div>' +
            '<div class="row"><button class="primary" data-act="save-watchdog">' +
              '保存守护设置</button></div>' +
            '<hr>' +
            '<h4>局域网共享（LAN 模式）</h4>' +
            '<div id="__wb_lan_box__">' +
              (lan ? lanHtml(lan) : '<div class="kv">正在读取局域网信息…</div>') +
            '</div>' +
          '</div>' +
          '<div class="foot">' +
            '<span class="hint">Esc 或点对话框外面也能关</span>' +
            '<button class="primary" data-act="cancel">关闭</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(wrap);

      // 把各节变成可折叠的手风琴。
      // 为什么用 JS 后处理而不是改 HTML 结构：那段 innerHTML 有 58 行、还被
      // 多处字符串拼出来（panelHtml / cands / warns），改成嵌套结构容易拼错；
      // 而"按 h4 + 后续兄弟节点切段"是纯 DOM 操作，抛异常也只是不折叠，
      // 设置页照常能用。
      try {
        buildCollapsibleSections(wrap);
      } catch (e) { /* 折叠是增强，坏了大不了全展开 */ }

      // 点对话框**外面**（空白遮罩）就关掉。用 mousedown 而不是 click：
      // "在框里按下、拖到外面松开"（选文字）不该被当成点空白处。
      wrap.addEventListener('mousedown', function (ev) {
        if (ev.target === wrap) { closeSettingsDialog(); }
      });

      // 之后那层看板这会儿不该再滚：锁住它，并把滚轮截在对话框里
      lockBackgroundScroll(true);

      var input = wrap.querySelector('#__wb_kdir_input__');
      var msg = wrap.querySelector('#__wb_kdir_msg__');
      var panelMsg = wrap.querySelector('#__wb_panel_msg__');
      var lanBox = wrap.querySelector('#__wb_lan_box__');
      var lanMsg = wrap.querySelector('#__wb_lan_msg__');

      function say(text, cls) {
        msg.className = 'msg' + (cls ? ' ' + cls : '');
        msg.textContent = text || '';
      }

      function sayPanel(text, cls) {
        panelMsg.className = 'msg' + (cls ? ' ' + cls : '');
        panelMsg.textContent = text || '';
      }

      function sayLan(text, cls) {
        if (!lanMsg) { return; }
        lanMsg.className = 'msg' + (cls ? ' ' + cls : '');
        lanMsg.textContent = text || '';
      }

      // ---- 局域网那一节：读取中 / 读到了 / 读不到（可重试）----------------
      function lanPlaceholderHtml(text) {
        return '<div class="kv">' + esc(text || '正在读取局域网信息…') + '</div>' +
          '<div class="row"><button data-act="lan-reload">重新读取</button></div>';
      }

      function applyLan(data) {
        if (!lanBox) { return; }
        lanBox.innerHTML = data ? lanHtml(data) : lanPlaceholderHtml();
        // 上面的替换把 #__wb_lan_msg__ 一起换掉了，重新取一次交给 sayLan
        lanMsg = wrap.querySelector('#__wb_lan_msg__');
      }

      function fillLanBox() {
        applyLan(null);
        withTimeout(call('lan_info'), 6000, '局域网信息').then(function (res) {
          applyLan(norm(res) || {});
        }).catch(function (err) {
          if (!lanBox) { return; }
          lanBox.innerHTML = lanPlaceholderHtml(
            '读取局域网信息失败：' + ((err && err.message) || err) +
            '——不影响上面那些设置。');
          lanMsg = wrap.querySelector('#__wb_lan_msg__');
        });
      }

      // 重新拉一次并把那一节重画。
      // 「保存并重启内核」之后内核要几秒才起来，所以这里带重试：live_lan 还是
      // 旧值就再过 1.5 秒问一次（最多 20 次，约 30 秒），别让界面停在"没生效"上。
      function refreshLan(attempt) {
        attempt = attempt || 0;
        call('lan_info').then(function (res) {
          var data = norm(res) || {};
          applyLan(data);
          var live = data.live_lan;
          if (live === null || live === undefined) { return; }
          var box = wrap.querySelector('#__wb_lan_toggle__');
          var want = !!(box && box.checked);
          if (live === want) { sayLan('已生效。', 'ok'); return; }
          if (attempt < 20) {
            setTimeout(function () { refreshLan(attempt + 1); }, 1500);
          }
        }).catch(function () { /* 拿不到就停在这一屏，不循环打扰 */ });
      }

      applyLan(lan);      // 打开时先用传入的那份（没有就先显示"读取中"）
      if (!lan) { fillLanBox(); }

      function validate() {
        return call('kernel_validate', { path: input.value }).then(function (r) {
          var out = norm(r);
          say((out && out.message) || '', out && out.ok ? 'ok' : 'bad');
          return !!(out && out.ok);
        });
      }

      function rememberChanged(ev) {
        var on = !!(ev.target && ev.target.checked);
        call('panel_remember_password', { remember: on }).then(function (r) {
          var out = norm(r);
          if (out && out.ok) {
            sayPanel(on ? ('已开启。记住密码：' +
                           (out.have_secret ? '已有' : '下次你手动输入的会被记下来'))
                        : '已关闭自动登录（已记住的密码也一并删掉了）',
                     'ok');
          } else {
            sayPanel((out && out.message) || '设置失败', 'bad');
          }
        }).catch(function (err) { sayPanel('设置失败：' + err.message, 'bad'); });
      }

      wrap.addEventListener('click', function (ev) {
        var cand = ev.target && ev.target.closest ? ev.target.closest('.cand') : null;
        if (cand) {
          var path = cand.getAttribute('data-path');
          if (path) { input.value = path; say(''); }
          return;
        }
        var act = ev.target && ev.target.getAttribute && ev.target.getAttribute('data-act');
        if (!act) { return; }
        if (act === 'cancel') { closeSettingsDialog(); return; }
        if (act === 'validate') { validate(); return; }
        if (act === 'save-watchdog') {
          var wdEnabled = wrap.querySelector('#__wb_wd_enabled__');
          var wdRetries = wrap.querySelector('#__wb_wd_retries__');
          var wdMsg = wrap.querySelector('#__wb_wd_msg__');
          var wantEnabled = !!(wdEnabled && wdEnabled.checked);
          var wantRetries = parseInt((wdRetries && wdRetries.value) || '3', 10);
          if (isNaN(wantRetries)) { wantRetries = 3; }
          if (wdMsg) { wdMsg.className = 'msg'; wdMsg.textContent = '正在保存…'; }
          withTimeout(call('watchdog_set', { enabled: wantEnabled,
                                             retries: wantRetries }), 8000,
                      '内核守护').then(function (res) {
            var out = norm(res) || {};
            if (wdMsg) {
              wdMsg.className = 'msg' + (out.ok ? ' ok' : ' bad');
              wdMsg.textContent = out.message || (out.ok ? '已保存' : '保存失败');
            }
          }).catch(function (err) {
            if (wdMsg) {
              wdMsg.className = 'msg bad';
              wdMsg.textContent = '保存失败：' + ((err && err.message) || err);
            }
          });
          return;
        }
        if (act === 'copy') {
          var copyBtn = ev.target;
          copyText(copyBtn.getAttribute('data-copy') || '', function (ok, why) {
            sayLan(ok ? '已复制到剪贴板'
                      : ('复制失败：' + (why || '请手动选中那串文字复制')),
                   ok ? 'ok' : 'bad');
            // 按钮自己也回一句话：那一节的提示在下面，用户可能正看着上面那几行，
            // 看不到反馈就会以为"没复制上"（这正是"无法复制"的第一印象）。
            try {
              var label = copyBtn.getAttribute('data-label') || copyBtn.textContent;
              copyBtn.setAttribute('data-label', label);
              copyBtn.textContent = ok ? '已复制' : '复制失败';
              setTimeout(function () { copyBtn.textContent = label; }, 1600);
            } catch (e) { /* 反馈失败不影响复制本身 */ }
          });
          return;
        }
        if (act === 'lan-reload') {
          sayLan('正在重新读取…');
          fillLanBox();
          return;
        }
        if (act === 'reveal') {
          var box = ev.target.closest ? ev.target.closest('.keybox') : null;
          var field = box ? box.querySelector('input') : null;
          if (!field) { return; }
          var real = field.getAttribute('data-real') || '';
          var hidden = field.getAttribute('data-masked') === '1';
          field.value = hidden ? real : maskKey(real);
          field.setAttribute('data-masked', hidden ? '0' : '1');
          ev.target.textContent = hidden ? '隐藏' : '显示';
          return;
        }
        if (act === 'lan-save' || act === 'lan-save-restart') {
          var lanBoxEl = wrap.querySelector('#__wb_lan_toggle__');
          var wantLan = !!(lanBoxEl && lanBoxEl.checked);
          var withRestart = (act === 'lan-save-restart');
          var doSaveLan = function () {
            sayLan(withRestart ? '正在保存并重启内核…' : '正在保存…');
            // 也加超时：保存本身可能已经落盘，但桥那头的回调掉了——
            // 那种情况不能让人一直看着"正在保存…"（重开设置就能看到真实状态）。
            withTimeout(call('lan_set', { lan: wantLan, restart: withRestart }),
                        30000, '外壳').then(
              function (r) {
                var out = norm(r);
                if (!out || !out.ok) {
                  sayLan((out && out.message) || '保存失败', 'bad');
                  return;
                }
                sayLan(out.message || '已保存', 'ok');
                refreshLan(0);
              }).catch(function (err) {
                sayLan('保存没有回话：' + ((err && err.message) || err) +
                       '——可能已经写进去了，重开设置看状态。', 'bad');
              });
          };
          if (withRestart) {
            // 重启会断几秒，先问一句；文案把代价说清楚（面板要重新登录）。
            showConfirm('保存并重启内核？', '局域网共享将改为：',
              (wantLan ? '开启（监听 0.0.0.0，强制 API Key）'
                       : '关闭（回到只监听本机）'),
              '保存会写进 data\\shell.json，然后重启内核（几秒中断）。' +
              '面板登录状态会失效——勾了「记住面板密码」的话外壳会替你自动登录。',
              doSaveLan);
          } else {
            doSaveLan();
          }
          return;
        }
        if (act === 'forget') {
          call('panel_remember_password', { forget: true }).then(function (r) {
            var out = norm(r);
            sayPanel((out && out.message) || '', out && out.ok ? 'ok' : 'bad');
          }).catch(function (err) { sayPanel('删除失败：' + err.message, 'bad'); });
          return;
        }
        if (act === 'save') {
          validate().then(function (fine) {
            if (!fine) { return; }
            // 自定义确认（不用 window.confirm：在 WebView2 里它会阻塞主线程，
            // 有时连对话框都不显示，点"保存"像是没反应）。
            showConfirm('保存并重启外壳？',
              '内核将记住位置：', input.value,
              '写入 bootstrap.json 后会重启外壳，内核会短暂中断（约 5 秒）。',
              function () {
                say('正在保存并重启…');
                call('kernel_set', { path: input.value }).then(function (r) {
                  var out = norm(r);
                  say((out && out.message) || '', out && out.ok ? 'ok' : 'bad');
                  if (!out || !out.ok) { return; }
                  // 外壳即将退出并重启：给个覆盖层，别让用户以为卡住。
                  // 用 .head/.body 结构——设置页的 .box 已经不带内边距了
                  // （内边距挪到了 .body 上，见 STYLE 里那两段注释）。
                  wrap.innerHTML = '<div class="box">' +
                    '<div class="head"><h3>正在重启外壳…</h3></div>' +
                    '<div class="body"><div class="kv">内核位置已保存。新外壳会在' +
                    '几秒内按新位置启动，本窗口会自动关闭。</div></div></div>';
                }).catch(function (err) {
                  say('保存失败：' + err.message, 'bad');
                });
              });
          });
        }
      });
      var box = wrap.querySelector('#__wb_remember__');
      if (box) { box.addEventListener('change', rememberChanged); }
    } catch (e) { toast('打开设置失败', true); }
  }

  // ---- 面板密码：自动登录 + 首次记住 --------------------------------------
  //
  // 为什么需要这个：面板的登录态是 **sessionStorage 里的 token**，而
  // sessionStorage 属于"这一个窗口"——外壳的 × 会销毁窗口（pywebview 拦不住
  // closing，见 README §一），托盘「显示窗口」是重建一个新窗口；更要命的是
  // token 在**内核内存里**，内核一重启必然失效（内核作者明确说这是有意的
  // 安全取舍）。所以"记住"只能记密码，在门出现时替用户输一次。
  //
  // 三条自律：只在用户勾选后记；每次页面加载**只试一次**（内核限流 5 次/60 秒）；
  // 任何一步失败都静默退回手动输入，绝不把看板弄坏。
  var panelAuto = { on: false, have: false, required: false, tried: false, pending: '' };

  function panelGateVisible() {
    var gate = document.getElementById('panelGate');
    if (!gate) { return false; }
    try {
      var style = window.getComputedStyle ? window.getComputedStyle(gate) : null;
      if (style && style.display === 'none') { return false; }
    } catch (e) { /* 取不到样式就按"可见"处理 */ }
    return true;
  }

  function panelTryAutoLogin() {
    if (!panelAuto.on || panelAuto.tried) { return; }
    if (!panelGateVisible()) { return; }
    var input = document.getElementById('panelPwdInput');
    if (!input || input.value) { return; }
    panelAuto.tried = true;
    call('panel_secret').then(function (res) {
      var out = norm(res);
      if (!out || !out.ok || !out.password) { return; }
      try {
        input.value = out.password;
        if (typeof window.submitPanelLogin === 'function') {
          window.submitPanelLogin();
        }
      } catch (e) { /* 退回手动 */ }
    }).catch(function () { /* 退回手动 */ });
  }

  function panelWatchGate() {
    var tries = 0;
    function attach() {
      var gate = document.getElementById('panelGate');
      if (!gate) {
        // 看板可能还没渲染出门来，等一会儿再找（最多约 10 秒）
        if (tries++ < 20) { setTimeout(attach, 500); }
        return;
      }
      try {
        if (window.MutationObserver) {
          new MutationObserver(panelTryAutoLogin)
            .observe(gate, {attributes: true, attributeFilter: ['style', 'class']});
        }
      } catch (e) { /* 观察失败也没关系，下面直接试一次 */ }
      panelTryAutoLogin();
    }
    attach();
  }

  function panelHookLogin() {
    // 门是看板自己的 UI；第一次还得用户手输。这里**只读**他输的那个值，
    // 且在"门关上了"（=登录成功）之后才交给外壳去记，避免把打错的密码存下来。
    if (typeof window.submitPanelLogin !== 'function') { return; }
    if (window.__wbShellLoginHooked) { return; }
    window.__wbShellLoginHooked = true;
    var original = window.submitPanelLogin;
    window.submitPanelLogin = function () {
      var typed = '';
      try {
        var input = document.getElementById('panelPwdInput');
        typed = (input && input.value) || '';
      } catch (e) { typed = ''; }
      var result = original.apply(this, arguments);
      // 每次**登录成功**都报一次（不只在"还没记过"时）：你在看板里改过密码
      // 之后，外壳记的就是旧口令——旧逻辑会一直拿旧口令去试、永远失败。
      // 要不要真的更新由后端按指纹判断（一样就不写盘），所以这里不用挑。
      if (typed && panelAuto.on) {
        try {
          Promise.resolve(result).then(function () {
            setTimeout(function () {
              if (!panelGateVisible()) {
                panelAuto.have = true;
                call('panel_remember_password', {password: typed});
              }
            }, 300);
          });
        } catch (e) { /* 忽略 */ }
      }
      return result;
    };
  }

  function panelPrepare(status) {
    panelAuto.on = !!(status && status.remember);
    panelAuto.have = !!(status && status.have_secret);
    panelAuto.required = !(status && status.required === false);
    panelAuto.tried = false;
    if (!panelAuto.on) { return; }
    panelHookLogin();
    panelWatchGate();
  }

  function esc(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // 自绘确认框。刻意不用 window.confirm：在 WebView2 里它会阻塞渲染线程，
  // 有时连框都不出现，用户点"保存"看起来像没反应。
  function showConfirm(title, line1, mono, line2, onYes) {
    try {
      var old = document.getElementById('__wb_confirm__');
      if (old) { old.remove(); }
      var wrap = document.createElement('div');
      // 用独立 id：如果复用 __wb_kdir__，确认框会把宿主对话框一起 remove 掉
      wrap.id = '__wb_confirm__';
      wrap.className = '__wb_modal__';
      wrap.innerHTML =
        '<div class="box" style="width:min(560px,92vw)">' +
          '<h3>' + esc(title) + '</h3>' +
          '<div class="kv">' + esc(line1) + '</div>' +
          '<div class="kv mono" style="word-break:break-all">' + esc(mono) + '</div>' +
          '<div class="kv" style="margin-top:8px">' + esc(line2) + '</div>' +
          '<div class="row">' +
            '<button data-act="no">取消</button>' +
            '<button class="primary" data-act="yes">继续</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(wrap);
      wrap.addEventListener('click', function (ev) {
        var act = ev.target && ev.target.getAttribute && ev.target.getAttribute('data-act');
        if (!act) { return; }
        wrap.remove();
        if (act === 'yes' && onYes) { onYes(); }
      });
    } catch (e) { /* 确认框失败就别挡路，直接放行 */ if (onYes) { onYes(); } }
  }

  // ---- 更新 -------------------------------------------------------------
  //
  // 分两步、中间隔着用户确认：先「检查」（只发现，不动盘），有新版才弹这个框；
  // 用户点"安装并重启内核"才真装。安装期间后端用 __wbShellUpdate 推进度，
  // 最终结果由这次 js_api 调用的返回值带回来——两条路各管一段，不重复渲染。
  function norm(res) {
    if (typeof res === 'string') {
      try { return JSON.parse(res); } catch (e) { return null; }
    }
    return res || null;
  }

  function updateFlow() {
    toast('正在检查更新…');
    call('update_check').then(function (res) {
      var info = norm(res);
      if (!info) { toast('检查更新返回异常', true); return; }
      if (!info.ok) { toast(info.message || '检查更新失败', true); return; }
      if (!info.available) {
        toast(info.message || ('已是最新版本 ' + (info.current_version || '')));
        return;
      }
      showUpdateDialog(info);
    }).catch(function (err) {
      toast('检查更新失败：' + ((err && err.message) || err), true);
    });
  }

  function showUpdateDialog(info) {
    try {
      var old = document.getElementById('__wb_update__');
      if (old) { old.remove(); }
      var notes = (info.notes || []).map(function (n) {
        return '<div class="kv warn">' + esc(n) + '</div>';
      }).join('');
      var log = String(info.changelog || '').trim();
      var wrap = document.createElement('div');
      wrap.id = '__wb_update__';
      wrap.className = '__wb_modal__';
      wrap.innerHTML =
        '<div class="box" style="width:min(680px,92vw)">' +
          '<h3>发现新版本 ' + esc(info.latest_version) + '</h3>' +
          '<div class="kv">当前 <b>' + esc(info.current_version) + '</b>' +
            '　→　新版本 <b>' + esc(info.latest_version) + '</b>' +
            (info.published_at
              ? '　（' + esc(String(info.published_at).slice(0, 10)) + '）' : '') +
          '</div>' +
          '<div class="kv">安装包 <b class="mono">' + esc(info.asset_name || '') + '</b>' +
            (info.size_text ? '　' + esc(info.size_text) : '') + '</div>' +
          '<div class="kv">校验值来源 <b>' + esc(info.sha_source || '未知') + '</b></div>' +
          notes +
          (info.can_install
            ? '<div class="kv warn">安装会先停止内核（本机短暂中断）。流程：下载 →' +
              ' SHA256 校验 → 强制备份 → 覆盖 → 自检；自检不过会自动回滚。</div>'
            : '<div class="kv bad">现在不能安装：' + esc(info.blocked_reason || '') +
              '</div>') +
          (log ? '<hr><div class="kv">更新说明：</div><div class="log">' + esc(log) +
                 '</div>' : '') +
          '<div class="row">' +
            '<button data-act="cancel">关闭</button>' +
            (info.can_install
              ? '<button class="primary" data-act="install">安装并重启内核</button>'
              : '') +
          '</div>' +
        '</div>';
      document.body.appendChild(wrap);
      wrap.addEventListener('click', function (ev) {
        var act = ev.target && ev.target.getAttribute &&
                  ev.target.getAttribute('data-act');
        if (!act) { return; }
        if (act === 'cancel') { wrap.remove(); return; }
        if (act === 'install') { wrap.remove(); startInstall(info); }
      });
    } catch (e) { toast('打开更新对话框失败', true); }
  }

  // 进度框与结果框共用同一个壳：装完就地变成结果面板，不叠两个模态框。
  function updateBox(title) {
    var old = document.getElementById('__wb_update__');
    if (old) { old.remove(); }
    var wrap = document.createElement('div');
    wrap.id = '__wb_update__';
    wrap.className = '__wb_modal__';
    wrap.innerHTML =
      '<div class="box" style="width:min(680px,92vw)">' +
        '<h3 class="u-title"></h3>' +
        '<div class="kv u-line"></div>' +
        '<div class="bar u-bar" style="display:none"><i></i></div>' +
        '<div class="log u-log" style="display:none"></div>' +
        '<div class="row u-row"></div>' +
      '</div>';
    document.body.appendChild(wrap);
    wrap.querySelector('.u-title').textContent = title || '';
    return wrap;
  }

  function startInstall(info) {
    var wrap = updateBox('正在更新内核');
    wrap.querySelector('.u-line').textContent = '准备安装 ' + (info.latest_version || '');
    call('update_install', { tag: info.tag }).then(function (res) {
      showUpdateResult(norm(res) || {});
    }).catch(function (err) {
      showUpdateResult({ok: false,
                        message: '安装请求失败：' + ((err && err.message) || err)});
    });
  }

  function showUpdateResult(out) {
    try {
      var wrap = document.getElementById('__wb_update__') || updateBox('更新');
      wrap.querySelector('.u-title').textContent = out.ok
        ? ('更新完成' + (out.to_version ? '：' + out.to_version : ''))
        : (out.rolled_back ? '更新失败（已自动回滚）' : '更新未完成');
      var line = out.message || '';
      if (out.ok) {
        // 说清"已经做了什么、还差什么"：内核在安装时就按新版本重启过了，
        // 真正还旧的是**这个页面**（它还是旧内核给的 HTML/JS）。
        // 外壳自身的版本显示会自己刷新，所以重启外壳是**可选项**，不是必需。
        line += '　内核已按新版本重启；这里只是页面还是旧内核给的界面——'
              + '重新加载看板即可用上新界面。';
      }
      wrap.querySelector('.u-line').textContent = line;
      wrap.querySelector('.u-bar').style.display = 'none';
      var steps = out.steps || [];
      if (steps.length) {
        var log = wrap.querySelector('.u-log');
        log.style.display = 'block';
        log.textContent = steps.join('\n');
      }
      var row = wrap.querySelector('.u-row');
      if (out.ok) {
        row.innerHTML =
          '<button data-act="later">稍后手动</button>' +
          '<button class="primary" data-act="reload">重新加载看板</button>' +
          '<button data-act="restart">重启外壳（再停一次内核）</button>';
      } else {
        row.innerHTML = '<button data-act="close">关闭</button>';
      }
      row.addEventListener('click', function (ev) {
        var act = ev.target && ev.target.getAttribute &&
                  ev.target.getAttribute('data-act');
        if (!act) { return; }
        if (act === 'close') { wrap.remove(); return; }
        if (act === 'later') {
          wrap.remove();
          toast('已留到以后：看板页下次加载就用上新界面');
          return;
        }
        if (act === 'reload') { wrap.remove(); call('reload_dashboard'); return; }
        if (act === 'restart') {
          wrap.innerHTML =
            '<div class="box"><h3>正在重启外壳…</h3>' +
            '<div class="kv">内核与外壳一起退出，随后自动重新拉起（约几秒）。' +
            '这个窗口会关闭，新窗口会自己出现；内核会短暂中断。</div></div>';
          // 页面马上会被销毁，所以这次调用的 promise 不一定会 resolve——
          // 不 catch 也无所谓，这里显式吞掉，别在控制台留一条看不懂的报错。
          call('restart_shell').catch(function () { /* 预期内 */ });
        }
      });
    } catch (e) { /* 结果面板失败也不改结论：完整步骤在 data\\shell.log 里 */ }
  }

  // 后端推来的安装进度。第一推可能早于确认框被替换成进度框，那就自己建一个——
  // 宁可早出现，也不要"点了没反应"。
  window.__wbShellUpdate = function (payload) {
    try {
      var p = payload || {};
      var wrap = document.getElementById('__wb_update__');
      if (!wrap || !wrap.querySelector('.u-line')) { wrap = updateBox('正在更新内核'); }
      var percent = p.total > 0
        ? Math.max(0, Math.min(100, p.done * 100 / p.total)) : -1;
      var text = p.text || p.stage || '';
      if (percent >= 0) { text += '　' + Math.round(percent) + '%'; }
      wrap.querySelector('.u-line').textContent = text;
      if (percent >= 0) {
        var bar = wrap.querySelector('.u-bar');
        bar.style.display = 'block';
        bar.querySelector('i').style.width = percent + '%';
      }
    } catch (e) { /* 进度推不进来不影响安装本身 */ }
  };

  function closeMenu(why) {
    try {
      var old = document.getElementById(MENU_ID);
      if (old) {
        old.remove();
        note('close:' + (why || '?'));
      }
    } catch (e) { /* 忽略 */ }
  }

  function menuItems() {
    // 每项：[动作, 标签, 快捷键提示, 是否可用]
    // 「重启外壳」与系统托盘菜单保持一致：跟在内核操作之后（2026-10-19
    // 用户反馈：托盘里改了，底栏这个菜单没改）。
    return [
      ['start', '启动内核', 'S', !!state.can_start],
      ['stop', '停止内核', 'T', !!state.can_stop],
      ['restart', '重启内核', 'R', !!state.can_restart],
      ['restart-shell', '重启外壳', '', !!state.can_restart_shell],
      [null, null, null, null],
      ['open', '打开看板（浏览器）', 'B', true],
      ['update', '检查更新', 'U', true],
      ['settings', '设置…', '', true],
      [null, null, null, null],
      ['quit', '退出', 'Q', true]
    ];
  }

  function openMenu(why) {
    closeMenu('reopen');
    try {
      note('open:' + (why || '?'));
      var menu = document.createElement('div');
      menu.id = MENU_ID;
      var head = document.createElement('div');
      head.className = 'wb-head';
      head.textContent = (state.state_label || '') +
        (state.port ? ' · 端口 ' + state.port : '') +
        (state.core_version ? ' · 内核 ' + state.core_version : '');
      menu.appendChild(head);

      menuItems().forEach(function (item) {
        if (item[0] === null) {
          var sep = document.createElement('div');
          sep.className = 'wb-sep';
          menu.appendChild(sep);
          return;
        }
        var act = item[0], label = item[1], key = item[2], enabled = item[3];
        var row = document.createElement('div');
        row.className = 'wb-mi' + (enabled ? '' : ' off');
        var name = document.createElement('span');
        name.textContent = label;
        var hint = document.createElement('span');
        hint.className = 'wb-key';
        hint.textContent = enabled ? key : '不可用';
        row.appendChild(name);
        row.appendChild(hint);
        if (enabled) {
          row.addEventListener('click', function (ev) {
            ev.stopPropagation();
            closeMenu('menuitem');
            run(act);
          });
        }
        menu.appendChild(row);
      });

      document.body.appendChild(menu);

      // 定位在状态区上方，并保证不越出窗口
      var anchor = document.querySelector('#__wb_shell_bar__ .wb-status');
      var rect = anchor ? anchor.getBoundingClientRect() : null;
      var mw = menu.offsetWidth, mh = menu.offsetHeight;
      var left = rect ? rect.left : 12;
      var top = rect ? (rect.top - mh - 6) : 12;
      left = Math.max(8, Math.min(left, window.innerWidth - mw - 8));
      top = Math.max(8, top);
      menu.style.left = left + 'px';
      menu.style.top = top + 'px';
    } catch (e) { console.warn('打开菜单失败', e); }
  }

  function build() {
    closeMenu();
    var bar = document.createElement('div');
    bar.id = '__wb_shell_bar__';
    // 底栏只留状态区：所有操作（含打开看板/检查更新/退出）都在右键菜单里，
    // 不在底栏重复一份。这样通知区能显示完整的状态信息，也不用维护两套按钮。
    bar.innerHTML =
      '<span class="wb-status" title="点击打开操作菜单（启动 / 停止 / 重启 / 看板 / 检查更新 / 内核位置 / 退出）">' +
        '<span class="wb-dot"></span>' +
        '<span class="wb-label">—</span>' +
        '<span class="wb-info"></span>' +
        '<span class="wb-caret">▾</span>' +
      '</span>' +
      '<span class="wb-toast" id="__wb_toast__"></span>';

    var status = bar.querySelector('.wb-status');
    // 左键与右键都打开菜单——左键**只开菜单，不执行任何动作**，
    // 所以既好用又不会误触把内核停掉/重启。
    status.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      openMenu('leftclick');
    });
    status.addEventListener('contextmenu', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      openMenu('contextmenu');
    });

    document.body.appendChild(bar);
    apply(state);
  }

  function apply(next) {
    try {
      if (next) { state = next; }
      var bar = document.getElementById('__wb_shell_bar__');
      if (!bar) { return; }
      var dot = bar.querySelector('.wb-dot');
      if (dot) { dot.style.background = COLORS[state.state] || COLORS.stopped; }
      var label = bar.querySelector('.wb-label');
      if (label) { label.textContent = state.state_label || state.state || ''; }
      var info = bar.querySelector('.wb-info');
      if (info) {
        info.textContent = [state.port ? ('端口 ' + state.port) : '',
          (state.accounts === null || state.accounts === undefined) ? ''
            : ('账号 ' + state.accounts +
               (state.accounts_ready === null ||
                state.accounts_ready === undefined ? '' : '/' + state.accounts_ready)),
          state.core_version ? ('内核 ' + state.core_version) : '',
          state.uptime ? ('运行 ' + state.uptime) : ''].filter(Boolean).join('  ·  ');
        // C：已知有新版本时，状态行末尾常驻一个「⬆ 版本」标记。
        // 2026-10-18 用户反馈：原来点了只是打开菜单，还得多点一步，看着像"假把式"。
        // 现在**点它直接进安装流程**（界面上那个带确认的更新对话框），
        // 与托盘「安装新版…」是同一个动作、同一套确认。
        if (state.pending_version) {
          var badge = document.createElement('span');
          badge.className = 'wb-update';
          badge.textContent = '⬆ ' + state.pending_version;
          badge.title = '有新内核版本 ' + state.pending_version
                        + '；点击安装（会先确认）';
          badge.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            // 直接走"检查更新"这条链：它会拿到 plan 并弹出带版本号与
            // 安装按钮的确认框（showUpdateDialog），不会静默开装。
            try { updateFlow(); } catch (e) { openMenu('leftclick'); }
          });
          if (info) {
            info.appendChild(document.createTextNode('  ·  '));
            info.appendChild(badge);
          }
        }
      }
    } catch (e) { /* 状态刷新失败不影响看板 */ }
  }

  // 菜单开着时：点别处 / 按 Esc 关掉；快捷键 S/T/R/B/U/Q
  document.addEventListener('click', function () { closeMenu('docclick'); }, true);
  document.addEventListener('contextmenu', function (ev) {
    if (!ev.target || !ev.target.closest || !ev.target.closest('#__wb_shell_bar__')) {
      closeMenu('docctx');
    }
  }, true);
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') {
      // 设置页优先：它开着的时候 Esc 关的是设置，而不是底下那个菜单
      if (closeSettingsDialog()) { return; }
      closeMenu('esc'); return;
    }
    if (ev.ctrlKey || ev.altKey || ev.metaKey) { return; }
    var tag = (ev.target && ev.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') { return; }
    var map = { s: 'start', t: 'stop', r: 'restart', b: 'open', u: 'update', q: 'quit' };
    var act = map[(ev.key || '').toLowerCase()];
    if (!act) { return; }
    var allow = { start: state.can_start, stop: state.can_stop,
                  restart: state.can_restart, open: true, update: true, quit: true };
    if (!allow[act]) { return; }
    ev.preventDefault();
    closeMenu('hotkey');
    run(act);
  });

  function install() {
    try {
      if (!document.getElementById('__wb_shell_style__')) {
        var style = document.createElement('style');
        style.id = '__wb_shell_style__';
        style.textContent = STYLE;
        document.head.appendChild(style);
      }
      document.body.style.paddingBottom = '44px';
      if (!document.getElementById('__wb_shell_bar__')) { build(); }
      // 面板密码：每次页面加载（含跳转后重新注入）都重新问一次外壳状态，
      // 该自动登录就自动登录（详见 panelPrepare 上方的说明）。
      call('panel_status').then(function (res) {
        try { panelPrepare(norm(res) || {}); } catch (e) { /* 不影响看板 */ }
      }).catch(function () { /* 拿不到就纯手动 */ });
      call('get_patches').then(function (res) {
        var out = typeof res === 'string' ? JSON.parse(res) : res;
        if (!out) { return; }
        if (out.css) {
          // 固定 id + 先删后插：切页签/重注入时**替换**而不是再插一份，
          // 否则每切一次页签就多一个 <style>，改一条规则也会因为旧的那份还在
          // 而"看起来没生效"。
          var oldStyle = document.getElementById('__wb_shell_patch_style__');
          if (oldStyle && oldStyle.parentNode) {
            oldStyle.parentNode.removeChild(oldStyle);
          }
          var s = document.createElement('style');
          s.id = '__wb_shell_patch_style__';
          s.textContent = out.css;
          document.head.appendChild(s);
        }
        if (out.js) {
          try { (new Function(out.js))(); }
          catch (e) { console.warn('dashboard.patch.js 执行失败', e); }
        }
      }).catch(function () { /* 补丁是可选件 */ });
    } catch (e) { console.warn('外壳控制层注入失败', e); }
  }

  ready(install);
  window.addEventListener('pywebviewready', install);

  // 自我修复：看板切换页面/重绘后底栏若被顶掉，这里补回来
  try {
    if (document.body && !window.__wbShellObserver) {
      window.__wbShellObserver = new MutationObserver(function () {
        try {
          if (!document.getElementById('__wb_shell_bar__')) { build(); }
        } catch (e) { /* 忽略 */ }
      });
      window.__wbShellObserver.observe(document.body, { childList: true });
    }
  } catch (e) { /* 忽略 */ }

  // 外壳主动推送状态
  window.__wbShellApply = apply;

  // 托盘「设置…」也走这里：外壳从后端推一句 `window.__wbShellSettings()` 即可。
  // 它调的是**同一个** settingsDialog()——不在托盘侧写第二份设置界面，
  // 少一份重复实现就少一份"两处不一致"的可能。
  window.__wbShellSettings = function () {
    try { settingsDialog(); return true; } catch (e) { return false; }
  };
})();
"""


def _fmt_size(size):
    """给人看的体积。拿不到就是空串，对话框少显示一行而不是显示 "0 MB"。"""
    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value < 1024 * 1024:
        return "%.0f KB" % (value / 1024.0)
    return "%.1f MB" % (value / 1048576.0)


def _fmt_uptime(seconds):
    seconds = int(max(0, seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return "%d天%02d:%02d" % (days, hours, minutes)
    return "%02d:%02d:%02d" % (hours, minutes, secs)


class WebWindow(object):
    """WebView2 单窗口。所有业务判断都问 service，UI 只呈现。"""

    def __init__(self, shell, config, effective, service, on_exit=None,
                 logger=None, tray=None, start_minimized=False):
        self.shell = shell
        self.config = config
        self.effective = effective
        self.service = service
        self.on_exit = on_exit
        self.log = logger or (lambda text: None)
        # 托盘句柄：决定"右上角的 ×"是直接关窗口（还能从托盘重开），
        # 还是必须先问一句（没有托盘就找不回窗口了）
        self.tray = tray
        # 由 _run_web 注入：退出语义的唯一裁决处
        self.coordinator = None
        self.start_minimized = bool(start_minimized)
        self.window = None
        self._closing = False          # 明确退出（不再重建窗口）
        self._hidden_to_tray = False
        self._tray_hint_shown = False
        self._last_state = ""
        # 注入层还没就绪时，把待推的状态先存着（下一轮重推，不丢）
        self._pending_state = None
        # 托盘「设置…」的待办：`--tray-only` 下点它时窗口还不存在，所以
        # 先记下意图，等窗口建好、注入层就绪后再把设置对话框推出去
        # （见 request_settings / _flush_settings_request）。
        self._settings_pending = False
        self._window_open = threading.Event()
        self._wake = threading.Event()
        # 窗口"关到托盘"后等托盘召唤的**轮询间隔**（秒）。
        # 它只是轮询间隔，**不是"多久之后自己把窗口开回来"**——超时不会重建
        # 窗口（见 run() 里那段注释：早先 600 秒超时后会自己弹回来）。
        # 测试把它调小，好在几秒内验证"关掉就不会自己回来"。
        self.hidden_wait = 600.0
        # evaluate_js 从两个后台线程（注入轮询、状态推送）调用，
        # 加一把锁把它们串起来，避免并发进同一个 WebView2 消息循环。
        self._inject_lock = threading.Lock()
        self._storage = os.path.join(paths.data_dir(), "webview-profile")
        # 看板补丁：**每次取都重读**（带 (mtime,size) 缓存），见 _read_patch。
        # 这样改 data\dashboard.patch.css 只要刷新看板就生效，不必重启外壳。
        self._patch_cache = {}
        # 更新：检查与安装分成两步，中间隔着用户确认。
        # `_update_plan` 是"检查过、用户看过的那一份"，安装只认它——
        # JS 能传的只有一个 tag，且必须与它对上（见 api_update_install）。
        self._update_lock = threading.Lock()
        self._update_plan = None
        self._update_busy = False
        # 进度推送限流用（下载阶段回调非常密，见 _push_update_progress）
        self._update_stage = ""
        self._update_push_at = 0.0

    # ---- 补丁覆盖层 -------------------------------------------------------

    def _read_patch(self, name):
        """读外壳自己的看板补丁（`data\\dashboard.patch.js` / `.css`）。

        带 `(mtime, size)` 缓存：`get_patches` 每次页面加载/换页签都会被问一次，
        不缓存就是每次都重读一遍文件。

        **改文件立刻生效**（下次注入就是新内容）——不需要重启外壳。这一点是
        2026-10-15 补上的：原来只在 `__init__` 里读一次，用户改一行 CSS 也得
        重启外壳，而那正好会中断内核。
        """
        path = os.path.join(paths.data_dir(), name)
        try:
            stat = os.stat(path)
            key = (stat.st_mtime_ns, stat.st_size)
        except Exception:
            self._patch_cache.pop(name, None)
            return ""
        cached = self._patch_cache.get(name)
        if cached is not None and cached[0] == key:
            return cached[1]
        text = ""
        try:
            # 上限只是防呆：别让人把一个几十 MB 的文件塞进注入层
            if stat.st_size < 512 * 1024:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            else:
                self.log("补丁 %s 太大（%d 字节），已忽略" % (name, stat.st_size),
                         "WARN")
        except Exception as exc:
            self.log("读取 %s 失败：%r" % (name, exc))
        self._patch_cache[name] = (key, text)
        return text

    # ---- 给注入层调用的 API ----------------------------------------------
    #
    # 注意：pywebview 调 js_api 方法时会**多传一个位置参数**（payload），
    # 所以每个暴露给 JS 的方法都必须接受 payload，哪怕不用它。
    # 签名不匹配时异常发生在 pywebview 内部，只在 stderr 里留下一行
    # "takes 1 positional argument but 2 were given"，界面看起来像"什么都没发生"。
    #
    # 另一条同样致命的规矩：**返回值必须能 json.dumps**。pywebview 就是拿
    # `json.dumps(result)` 往回传的，混进一个不能序列化的东西（例如 service
    # 里那个**打开的内核日志文件对象**）会让**整个接口**失效，界面只看到
    # "报错"或"点了没反应"。2026-10-15 实测栽过一次（api_lan_info 用了
    # status()["log_file"]，真报错是 "Object of type TextIOWrapper is not
    # JSON serializable"）。要路径请用 service._log_path()。

    def api_get_state(self, payload=None):
        return self._state_payload()

    def api_watchdog_get(self, payload=None):
        """读「内核守护」的当前设置（设置页那一节用）。"""
        cfg = self.config or {}
        return {"ok": True,
                "enabled": bool(cfg.get("core_watchdog", True)),
                "retries": int(cfg.get("core_watchdog_retries", 3) or 0),
                "note": self.service.status().get("watchdog_note") or "",
                "restart_count": self.service.status().get("restart_count") or 0}

    def api_watchdog_set(self, payload=None):
        """改「内核守护」开关/重试次数：只写外壳自己的 shell.json，立刻生效。

        立刻生效的做法：service 上那三个属性是运行期读的（`_on_core_crashed`
        才读），所以这里同时改内存，免得"保存了却要等重启"。
        """
        payload = payload or {}
        cfg = self.config or {}
        want = bool(payload.get("enabled", cfg.get("core_watchdog", True)))
        retries = payload.get("retries", cfg.get("core_watchdog_retries", 3))
        try:
            retries = max(0, min(10, int(retries)))
        except Exception:
            retries = 3
        was = (bool(cfg.get("core_watchdog", True)),
               int(cfg.get("core_watchdog_retries", 3) or 0))
        if (want, retries) == was:
            return {"ok": True, "changed": False,
                    "message": "内核守护设置本来就是%s" % ("开启" if want else "关闭")}
        cfg["core_watchdog"] = want
        cfg["core_watchdog_retries"] = retries
        try:
            config_mod.backup_before_overwrite(self.shell)
        except Exception:
            pass
        ok, message = config_mod.save(self.config, self.shell)
        if not ok:
            cfg["core_watchdog"], cfg["core_watchdog_retries"] = was
            self.log("内核守护：写入 shell.json 失败：%s" % message, "WARN")
            return {"ok": False,
                    "message": "没保存成功：%s（配置保持原样）" % message}
        try:
            self.service.watchdog = want
            self.service.watchdog_retries = retries
        except Exception:
            pass
        self.log("内核守护：%s，最多自动重启 %d 次（已写入 shell.json）"
                 % ("开启" if want else "关闭", retries))
        return {"ok": True, "changed": True,
                "message": "内核守护已%s（最多自动重启 %d 次）"
                           % ("开启" if want else "关闭", retries)}

    def api_get_patches(self, payload=None):
        """把看板补丁交给注入层。每次调用都重读（带缓存）——改完不用重启外壳。"""
        return {"ok": True,
                "js": self._read_patch("dashboard.patch.js"),
                "css": self._read_patch("dashboard.patch.css")}

    def api_action(self, payload=None):
        name = str((payload or {}).get("name") or "")
        try:
            if name == "start":
                ok, message = self.service.start()
            elif name == "stop":
                ok, message = self.service.stop()
            elif name == "restart":
                ok, message = self.service.restart()
            elif name == "restart-shell":
                # 底栏菜单的「重启外壳」（2026-10-19 用户要求与托盘菜单对齐）。
                # 直接复用已有接口——它走同一条协调器链路：停内核、拉起新外壳。
                info = self.api_restart_shell()
                ok, message = bool(info.get("ok")), info.get("message") or ""
            elif name == "update":
                # 兼容入口：老注入层/外部调用只认 action('update')。
                # 它**只检查**，不安装——安装只能走 api_update_install。
                info = self.api_update_check()
                ok, message = bool(info.get("ok")), info.get("message") or ""
            else:
                ok, message = False, "未知操作：%s" % name
        except Exception as exc:
            ok, message = False, "%s: %s" % (type(exc).__name__, exc)
        self.log("控制层操作 %s → %s" % (name, message))
        return {"ok": bool(ok), "message": message}

    def api_open_dashboard(self, payload=None):
        import webbrowser
        url = self.service.status()["dashboard"]
        try:
            webbrowser.open(url)
            return {"ok": True, "message": "已在系统浏览器打开"}
        except Exception as exc:
            return {"ok": False, "message": "打开失败：%s" % exc}

    def api_quit(self, payload=None):
        self.log("控制层请求退出")
        if self.on_exit is not None:
            self.on_exit()
        return {"ok": True, "message": "正在退出"}

    # ---- 内核位置 ---------------------------------------------------------

    def api_kernel_info(self, payload=None):
        """给「内核位置」对话框：当前用的目录、认来的途径、所有候选。"""
        coordinator = getattr(self, "coordinator", None)
        if coordinator is not None and hasattr(coordinator, "kernel_info"):
            return coordinator.kernel_info()
        import paths as paths_mod
        core = self.service.core_dir
        return {"ok": True, "shell_dir": self.shell, "core_dir": core,
                "source": (self.effective or {}).get("core_source") or "",
                "version": self.service.status().get("core_version") or "",
                "valid": paths_mod.is_core_dir(core),
                "problem": paths_mod.describe_core_problem(core),
                "bootstrap": paths_mod.bootstrap_path(),
                "has_bootstrap": os.path.isfile(paths_mod.bootstrap_path()),
                "candidates": paths_mod.candidate_core_dirs(self.shell)}

    def api_kernel_validate(self, payload=None):
        """校验用户填的路径，返回 (ok, message)，供对话框即时反馈。"""
        candidate = str((payload or {}).get("path") or "")
        coordinator = getattr(self, "coordinator", None)
        if coordinator is not None and hasattr(coordinator, "validate_kernel_dir"):
            ok, message = coordinator.validate_kernel_dir(candidate)
        else:
            import paths as paths_mod
            text = candidate.strip().strip('"')
            if not text:
                ok, message = False, "路径为空"
            elif not os.path.isdir(text):
                ok, message = False, "目录不存在：%s" % text
            elif paths_mod.is_core_dir(text):
                ok, message = True, "可用"
            else:
                ok, message = False, paths_mod.describe_core_problem(text)
        return {"ok": bool(ok), "message": message, "path": candidate}

    def api_kernel_set(self, payload=None):
        """记住新的内核位置。成功后外壳会重启，所以这里要主动关掉窗口循环。"""
        candidate = str((payload or {}).get("path") or "")
        coordinator = getattr(self, "coordinator", None)
        if coordinator is None or not hasattr(coordinator, "set_kernel_dir"):
            return {"ok": False, "message": "当前外壳不支持修改内核位置"}
        result = coordinator.set_kernel_dir(candidate)
        self.log("设置内核位置 %s → %s" % (candidate, result.get("message")))
        if result.get("ok"):
            # 让窗口循环退出，外层看到 wants_relaunch 就重启外壳
            self._closing = True
            try:
                if self.window is not None:
                    self.window.destroy()
            except Exception:
                pass
        return result

    # ---- 局域网共享（LAN 模式）--------------------------------------------
    #
    # 内核侧早就实现了（`--lan` → `--host 0.0.0.0` + 强制 API Key，Key 存进
    # `<accounts>\settings.json`），外壳也早就把 `--lan` 传给内核了——缺的只是
    # **界面**：以前要开 LAN 只能手编 shell.json，而且 Key 只印在内核启动 banner
    # 里（被外壳抓进 data\core-<port>.log，交接文档 §6.14）。
    #
    # 这一段**只读内核**：Key 从 accounts\settings.json 读，绝不写内核目录。
    # 「开/关」写的是外壳自己的 shell.json（原子写，走 config.save）。

    def api_copy(self, payload=None):
        """把一段文字放进 Windows 剪贴板——注入层复制按钮的**兜底**那条路。

        浏览器那两条（execCommand / clipboard API）在 WebView2 里都可能失败，
        而且"异步失败之后再 execCommand"会丢用户手势（见 CONTROL_BAR_JS 里
        copyText 的注释）。外壳自己写剪贴板不看浏览器脸色。

        ⚠️ 日志里**绝不写正文**（那可能是 API Key），只记长度。
        """
        text = str((payload or {}).get("text") or "")
        if not text:
            return {"ok": False, "message": "没有要复制的内容"}
        try:
            import clipboard as clipboard_mod
        except Exception as exc:
            return {"ok": False, "message": "剪贴板模块不可用：%s" % exc}
        ok, message = clipboard_mod.set_text(text)
        self.log("复制到剪贴板：%s（%d 个字符）"
                 % ("成功" if ok else "失败", len(text)))
        return {"ok": bool(ok), "message": message}

    def _lan_cfg(self):
        return self.config if isinstance(self.config, dict) else {}

    def _lan_eff_cfg(self):
        return ((self.effective or {}).get("config") or {})

    def _core_state(self):
        try:
            return str((self.service.status() or {}).get("state") or "")
        except Exception:
            return ""

    def api_lan_info(self, payload=None):
        import lanshare
        cfg = self._lan_cfg()
        eff_cfg = self._lan_eff_cfg()
        accounts_dir = (self.effective or {}).get("accounts_dir") or ""
        config_lan = bool(cfg.get("lan"))
        effective_lan = bool(eff_cfg.get("lan"))
        try:
            status = self.service.status() or {}
        except Exception:
            status = {}
        port = int(status.get("port") or getattr(self.service, "port", 0) or 0)
        state = str(status.get("state") or "")
        external = state == service_mod.EXTERNAL

        # 运行中的内核到底绑没绑 0.0.0.0：外壳自己启动的才说得准（`self.command`
        # 是**启动那一刻**拼好的那份，之后不会变），companion 模式只能给"未知"。
        live_lan = None
        if not external:
            command = list(status.get("command")
                           or getattr(self.service, "command", []) or [])
            if command:
                live_lan = "--lan" in command
            elif state in (service_mod.RUNNING, service_mod.STARTING):
                live_lan = bool(getattr(self.service, "lan", False))

        ips = lanshare.local_ipv4_addresses()
        keys = lanshare.keys_state(accounts_dir, cfg.get("api_key"))
        primary_key = ((keys.get("accepted") or [{}])[0] or {}).get("key") or ""
        rows = lanshare.links(ips, port)
        for row in rows:
            row["dashboard_keyed"] = lanshare.dashboard_url_with_key(
                row["ip"], port, primary_key)

        # 内核日志**路径**（不是那个打开的文件对象，见下面返回处的注释）
        core_log = ""
        try:
            core_log = self.service._log_path()
        except Exception:
            core_log = ""

        # 端到端自检：真的从内网地址问一次 /health。配置里写着 lan=true 不算数，
        # 防火墙照样能把包丢掉——所以这条证据比读配置硬。
        reachable = ""
        checked = False
        if ips and port and state == service_mod.RUNNING:
            checked = True
            reachable = lanshare.reachable_ip(ips, port)

        return {
            "ok": True,
            "config_lan": config_lan,
            "effective_lan": effective_lan,
            # 配置值 ≠ 生效值 = 被 --lan / bootstrap.json 顶掉了
            "cli_override": config_lan != effective_lan,
            "live_lan": live_lan,
            "needs_restart": (live_lan is not None
                              and bool(effective_lan) != bool(live_lan)),
            "core_state": state,
            "core_label": status.get("state_label") or "",
            "core_external": external,
            "port": port,
            "ips": ips,
            "links": rows,
            "key": keys,
            "reachable": reachable,
            "checked_reach": checked,
            "firewall": lanshare.firewall_hint(self.service.core_dir, port),
            # ⚠️ 这里**不能**用 status()["log_file"]：那是内核日志的**打开的文件对象**
            # （service._open_log_file），塞进 js_api 的返回值会让 pywebview 的
            # json.dumps 当场抛 "Object of type TextIOWrapper is not JSON serializable"，
            # 于是整个接口失效——2026-10-15 实测：局域网那一节就只剩一句错误提示。
            # 要路径就用 `_log_path()`。
            "core_log": core_log,
            "accounts_settings": lanshare.accounts_settings_path(accounts_dir),
        }

    def api_lan_set(self, payload=None):
        """把 `lan` 写进 shell.json（可选顺带重启内核让它生效）。

        只改外壳自己的配置与内存里的 service 标志；**内核一个字都不动**——
        真正的 LAN 行为要等内核按新参数重启之后才有（`--lan`）。
        """
        payload = payload or {}
        want = bool(payload.get("lan"))
        restart = bool(payload.get("restart"))
        cfg = self._lan_cfg()
        eff_cfg = self._lan_eff_cfg()

        if bool(cfg.get("lan")) != bool(eff_cfg.get("lan")):
            return {"ok": False, "restarting": False,
                    "message": "启动参数（--lan）或 bootstrap.json 覆盖了这一项，"
                               "它优先——改 shell.json 不会生效。先去把那个参数去掉。"}

        was = bool(cfg.get("lan"))
        # 值没变就不写盘：写完 mtime 会变，"探针只读 / 我没动配置"这类复核就白做了；
        # 也让重复点「只保存」不会反复制造 config-backups。
        if bool(want) != was:
            cfg["lan"] = want
            eff_cfg["lan"] = want
            # 写盘前留一份原件（当天只留一次），与其它设置改动同一套动作
            try:
                config_mod.backup_before_overwrite(self.shell)
            except Exception:
                pass
            ok, message = config_mod.save(self.config, self.shell)
            if not ok:
                cfg["lan"] = was
                eff_cfg["lan"] = was
                self.log("局域网共享：写入 shell.json 失败：%s" % message, "WARN")
                return {"ok": False, "restarting": False,
                        "message": "没保存成功：%s（配置保持原样）" % message}
            # 内存里也要跟上：Service 的 lan 是构造时读进去的，下次 build_command
            # 用它拼 --lan（否则"保存了却不生效"就要再等一次外壳重启）。
            try:
                self.service.lan = want
                self.service.effective["config"]["lan"] = want
            except Exception:
                pass
            self.log("局域网共享：%s（已写入 shell.json%s）"
                     % ("开启" if want else "关闭",
                        "；随即重启内核" if restart else "；等重启内核后生效"))
            saved_note = "已保存（%s）。" % ("开启" if want else "关闭")
        else:
            self.log("局域网共享：配置本来就是%s，不写盘%s"
                     % ("开启" if want else "关闭",
                        "；仍按要求重启内核" if restart else ""))
            saved_note = "配置本来就是%s，没有改动。" % ("开启" if want else "关闭")

        if not restart:
            return {"ok": True, "restarting": False,
                    "message": saved_note + "重启内核后生效。"}

        if self._core_state() == service_mod.EXTERNAL:
            return {"ok": True, "restarting": False,
                    "message": saved_note + "但这个内核是别处启动的，外壳不能替你"
                               "重启它——请在你启动它的那个窗口里重启。"}

        threading.Thread(target=self._restart_core_after_lan, daemon=True,
                         name="wb-lan-restart").start()
        return {"ok": True, "restarting": True,
                "message": saved_note + "正在重启内核（几秒），面板需要重新登录…"}

    def _restart_core_after_lan(self):
        """在后台重启内核（不能占着 js_api 那条路等，界面要立刻有反馈）。"""
        try:
            ok, message = self.service.restart()
        except Exception as exc:
            ok, message = False, "%s: %s" % (type(exc).__name__, exc)
        self.log("局域网共享：内核重启%s（%s）"
                 % ("成功" if ok else "失败", message))
        if not ok:
            try:
                if self.tray is not None:
                    self.tray.notify("内核重启失败：%s" % message,
                                     "局域网共享改动还没生效")
            except Exception:
                pass

    # ---- 面板密码（"记住" = 自动登录）---------------------------------------
    #
    # 面板的登录态是**内核内存里的 token**：内核一重启就失效（内核作者明确
    # 说那是有意的安全取舍），而窗口重建又会丢掉 sessionStorage（token 存在
    # 那里，属于"这一个窗口"）。所以"记住"只能记**密码**：用 Windows DPAPI
    # 按当前用户加密存（见 secret_store.py），门出现时替用户输一次。
    #
    # 三条自律：
    #   1. 只在用户勾选「记住面板密码」后才写入；
    #   2. 取口令的接口（panel_secret）在开关关着时**一律拒绝**——它等价于
    #      一次"取口令"，不能让看板上的任意脚本白拿；
    #   3. 任何一步失败都退回手动输入，绝不把看板弄坏。

    def _panel_remember_enabled(self):
        return bool(self.config.get("panel_password_remember"))

    def _panel_status_from_kernel(self):
        try:
            return service_mod.probe_panel_status(self.service.port, self.service.host)
        except Exception:
            return {}

    def api_panel_status(self, payload=None):
        """给注入层与设置对话框：要不要密码、记没记住、什么时候记的。"""
        kernel = self._panel_status_from_kernel()
        return {
            "ok": True,
            "remember": self._panel_remember_enabled(),
            "have_secret": secret_store.has(),
            "saved_at": secret_store.saved_at(),
            "required": kernel.get("panel_password_required"),
            "is_default": kernel.get("panel_password_is_default"),
            "authenticated": kernel.get("authenticated"),
            "sealed_path": secret_store.secret_path(),
        }

    def api_panel_secret(self, payload=None):
        """把记住的密码交给页面去自动登录（开关关着就拒绝）。"""
        if not self._panel_remember_enabled():
            return {"ok": False, "message": "没有开启「记住面板密码」"}
        text, message = secret_store.load()
        if not text:
            return {"ok": False, "message": message or "还没有记住密码"}
        # 留一行审计：自动登录"发生过"这件事要能从日志里看出来（不含密码本身）
        self.log("面板密码：交给页面自动登录（指纹 %s）"
                 % config_mod.secret_fingerprint(text))
        return {"ok": True, "password": text}

    def api_panel_remember_password(self, payload=None):
        """设置/取消「记住面板密码」，或者把刚输入的密码记下来。

        payload：
          remember=true/false → 开关（关掉时连已记住的一起删掉）
          forget=true         → 只删已记住的，开关不动
          password="..."      → 记住这个密码（只在开关打开时接受）
        """
        payload = payload or {}
        config = self.config
        if payload.get("forget"):
            ok, message = secret_store.clear()
            self.log("面板密码：忘记已记住的密码 → %s" % message)
            return {"ok": ok, "message": message,
                    "have_secret": secret_store.has(),
                    "remember": self._panel_remember_enabled()}
        if "remember" in payload:
            wanted = bool(payload.get("remember"))
            config["panel_password_remember"] = wanted
            saved, detail = config_mod.save(config, self.shell)
            message = "已开启自动登录" if wanted else "已关闭自动登录"
            if not wanted:
                secret_store.clear()
                message += "，并删掉了已记住的密码"
            self.log("面板密码：%s（写盘 %s）"
                     % (message, "成功" if saved else "失败：%s" % detail))
            return {"ok": bool(saved), "message": message if saved else detail,
                    "have_secret": secret_store.has(), "remember": wanted}
        password = payload.get("password")
        if not password:
            return {"ok": False, "message": "没有收到密码"}
        if not self._panel_remember_enabled():
            return {"ok": False, "message": "没有开启「记住面板密码」"}
        old, _ = secret_store.load()
        if old and (config_mod.secret_fingerprint(old)
                    == config_mod.secret_fingerprint(password)):
            # 已经记着的就是这个密码：不重复写盘（每次页面加载都会回调到这里）
            return {"ok": True, "message": "密码没变，不用重记",
                    "have_secret": True, "remember": True}
        ok, message = secret_store.save(password)
        self.log("面板密码：记住 → %s（指纹 %s）"
                 % ("成功" if ok else "失败",
                    config_mod.secret_fingerprint(password)))
        return {"ok": ok, "message": message, "have_secret": secret_store.has(),
                "remember": True}

    def _can_restart_shell(self):
        """底栏「重启外壳」能不能用。判据与 api_restart_shell 完全一致，
        两处必须同一个真相：能点却报"不支持"比置灰更糟。"""
        coordinator = getattr(self, "coordinator", None)
        return (coordinator is not None
                and hasattr(coordinator, "request_shell_restart"))

    def api_restart_shell(self, payload=None):
        """退出内核与外壳、再拉起一个新外壳（更新之后「重启应用」）。

        走的是「内核位置」那条已经验证过的重启链路。注意：调用方**不要等
        这个返回值**——外壳会主动销毁窗口，这次 js_api 调用的 promise 很可能
        永远不 resolve（页面已经没了）。界面应该在调它之前就把"正在重启"画上。
        """
        coordinator = getattr(self, "coordinator", None)
        if coordinator is None or not hasattr(coordinator, "request_shell_restart"):
            return {"ok": False, "message": "当前外壳不支持重启"}
        result = coordinator.request_shell_restart("更新后重启外壳")
        self.log("重启外壳 → %s" % result.get("message"))
        if result.get("ok"):
            self._closing = True
            try:
                if self.window is not None:
                    self.window.destroy()
            except Exception:
                pass
        return result

    def api_reload_dashboard(self, payload=None):
        """重新加载看板页：更新之后页面还是旧内核给的界面，重载即用上新的。"""
        url = self.service.status()["dashboard"]
        ok = self._eval_js("window.location.replace(%s)" % json.dumps(url))
        return {"ok": bool(ok), "message": "正在重新加载看板" if ok else "重载失败",
                "url": url}

    # ---- 更新 -------------------------------------------------------------
    #
    # 分两步、中间隔着用户确认：
    #   update_check   → 只发现（联网 + 读本地），返回一份"能不能装"的说明；
    #   update_install → 真安装，跑在同一次 js_api 调用的线程里（pywebview 每个
    #                    js_api 调用都是独立线程，所以不会冻界面），
    #                    进度用 __wbShellUpdate 推回页面。
    # 安装只认 update_check 存下来的那份 plan，JS 能传的只有 tag 且必须对上——
    # 校验值、下载地址一律不从界面收。

    def _make_updater(self, updater_mod):
        return updater_mod.Updater(self.effective, self.service, self.shell)

    def api_update_check(self, payload=None):
        """检查更新：只发现，不动盘。返回 UpdatePlan 的字段 + 能否安装。"""
        try:
            import updater as updater_mod
        except Exception as exc:
            return {"ok": False, "message": "更新模块不可用：%s" % exc}
        try:
            plan = self._make_updater(updater_mod).check()
        except updater_mod.UpdateError as exc:
            return {"ok": False, "message": "检查更新失败：%s" % exc}
        except Exception as exc:
            return {"ok": False,
                    "message": "%s: %s" % (type(exc).__name__, exc)}
        if not plan.latest_version:
            return {"ok": False, "message": "未能识别最新版本"}
        if not plan.available:
            with self._update_lock:
                self._update_plan = None
            return {"ok": True, "available": False,
                    "message": "已是最新版本 %s" % plan.current_version,
                    "current_version": plan.current_version,
                    "latest_version": plan.latest_version}

        blocked = updater_mod.install_blocked_reason(plan, self.service)
        with self._update_lock:
            self._update_plan = plan
        message = "发现新版本 %s（当前 %s）" % (plan.latest_version,
                                              plan.current_version)
        self.log("检查更新 → %s%s" % (message, ("；" + blocked) if blocked else ""))
        info = plan.as_dict()
        info.update({
            "ok": True,
            "message": message + ("；" + blocked if blocked else ""),
            "can_install": not blocked,
            "blocked_reason": blocked,
            "size_text": _fmt_size(plan.asset_size),
            "core_dir": self.service.core_dir,
        })
        return info

    def api_update_install(self, payload=None):
        """安装更新。这是唯一会动内核目录的界面入口。"""
        payload = payload or {}
        try:
            import updater as updater_mod
        except Exception as exc:
            return {"ok": False, "message": "更新模块不可用：%s" % exc}
        with self._update_lock:
            if self._update_busy:
                return {"ok": False, "message": "已经有一个更新在执行中，请等它结束"}
            plan = self._update_plan
            if plan is None:
                return {"ok": False, "message": "请先点「检查更新」，再安装"}
            tag = str(payload.get("tag") or "")
            if tag and tag != plan.tag:
                return {"ok": False,
                        "message": "界面要装的版本（%s）与刚检查出来的（%s）不一致，"
                                   "请重新检查更新" % (tag, plan.tag)}
            blocked = updater_mod.install_blocked_reason(plan, self.service)
            if blocked:
                return {"ok": False, "message": blocked}
            self._update_busy = True
        try:
            worker = self._make_updater(updater_mod)
            result = worker.install(plan, progress=self._push_update_progress)
        except Exception as exc:
            result = {"ok": False, "steps": [],
                      "message": "%s: %s" % (type(exc).__name__, exc)}
        finally:
            with self._update_lock:
                self._update_busy = False
        if result.get("ok"):
            with self._update_lock:
                self._update_plan = None
        steps = result.get("steps") or []
        if len(steps) > 12:
            steps = ["…（前 %d 步略）" % (len(steps) - 12)] + steps[-12:]
        result["steps"] = steps
        self.log("安装更新 → %s" % (result.get("message") or result.get("ok")))
        return result

    def _push_update_progress(self, stage, done=0, total=0, text=""):
        """把安装进度推给注入层（同一条 evaluate_js 通道，跨线程调用）。

        **限流到 ~0.25 秒一次**：下载阶段每 256 KB 就回调一次，而
        `evaluate_js` 是同步的——每次都推等于让下载线程反复去等 WebView2
        的 UI 线程。2026-10-14 那两次"校验失败"就是下载途中被切断的，
        虽然不能全赖在这里，但让下载线程少去等界面是实打实的好处。
        阶段变了、或这一阶段跑完了，一定推（进度条不会卡在旧值上）。
        """
        done = int(done or 0)
        total = int(total or 0)
        stage = stage or ""
        now = time.time()
        finished = bool(total and done >= total)
        with self._update_lock:
            if (stage == self._update_stage
                    and not finished
                    and now - self._update_push_at < 0.25):
                return
            self._update_stage = stage
            self._update_push_at = now
        payload = {"busy": True, "stage": stage, "text": text or "",
                   "done": done, "total": total}
        self._eval_js("window.__wbShellUpdate && window.__wbShellUpdate(%s)"
                      % json.dumps(payload, ensure_ascii=False))

    # ---- 内部 -------------------------------------------------------------

    def _eval_js(self, code):
        """从任意线程往页面推一段 JS。注入层没就绪就当作没推成，不抛、不阻塞。

        与 _inject_bar / _sync_state_loop 共用同一把锁：并发的 evaluate_js
        进的是同一个 WebView2 消息循环，串起来更稳（那两处已经这么做）。
        """
        window = self.window
        if window is None:
            return False
        try:
            with self._inject_lock:
                window.evaluate_js(code)
            return True
        except Exception as exc:
            self.log("推送 JS 失败：%r" % (exc,), "WARN")
            return False

    def _state_payload(self):
        status = self.service.status()
        state = status["state"]
        uptime = _fmt_uptime(status["uptime"]) if status["uptime"] else ""
        return {
            "state": state,
            "state_label": status["state_label"],
            "detail": status["detail"],
            "port": status["port"],
            "accounts": status.get("accounts"),
            "accounts_ready": status.get("accounts_ready"),
            "realm": status.get("realm"),
            "core_version": status.get("core_version"),
            "uptime": uptime,
            "can_start": state in (service_mod.STOPPED, service_mod.FAILED),
            "can_stop": state in (service_mod.RUNNING, service_mod.STARTING),
            "can_restart": state in (service_mod.RUNNING, service_mod.EXTERNAL),
            # 底栏「重启外壳」是否可用。判断依据与 api_restart_shell 一致：
            # 有协调器才能"停内核 + 拉起新外壳"；没有就置灰，比"点了没反应"好。
            "can_restart_shell": self._can_restart_shell(),
            # C：已知有新版本时给底栏一个常驻标记。**不联网**——只读
            # update-state.json（cached_plan），所以每 0.5 秒推一次状态也不花钱。
            "pending_version": self._pending_update_version(),
        }

    def _pending_update_version(self):
        """缓存里"已知的新版本"（没有就返回空串）。纯本地读，不联网。"""
        try:
            import updater as updater_mod
            plan = updater_mod.Updater(self.effective, self.service,
                                       self.shell).cached_plan()
            if plan is not None:
                return plan.latest_version
        except Exception:
            pass
        return ""

    def _inject_bar(self):
        """把控制层推进页面。返回 True 表示这次 evel 执行成功。

        pywebview 6.x 没有 `inject_js` 参数（那是旧版 API），注入只能靠
        `window.evaluate_js`。所以这里的策略是**按页面地址变化重注入**：
        登录跳转、切换页签都会换地址，换一次注一次；脚本自身幂等，
        重复执行只是白跑一次判断。
        """
        if self.window is None:
            return False
        with self._inject_lock:
            try:
                self.window.evaluate_js(CONTROL_BAR_JS)
                return True
            except Exception as exc:
                logging_setup.log("控制层注入失败：%r" % (exc,), "WARN")
                return False

    def _watch_loop(self):
        """轮询当前页面地址；地址变了就重注入控制层。"""
        last_url = ""
        while not self._closing:
            try:
                url = ""
                if self.window is not None:
                    url = self.window.get_current_url() or ""
                if url and url != last_url:
                    last_url = url
                    self.log("页面已切换：%s" % url)
                    self._inject_bar()
            except Exception:
                pass
            time.sleep(1.0)

    def _sync_state_loop(self):
        """把状态推给注入层。变化才推，避免无谓的 JS 调用。

        两个坑都在这里踩过：

        1. `evaluate_js("window.__wbShellApply && window.__wbShellApply(x)")`
           在**注入层还没装好时不会报错**——`&&` 短路成 undefined。所以只看
           有没有抛异常是不够的，必须**确认 `__wbShellApply` 真的存在**再推。
        2. 早先的写法在推送**之前**就写 `_last_state`；一旦这次没推成，标记已置位，
           这一版状态就永远不再推送，界面上状态区会一直空着。
        两条合起来就是：先确认可推，再推，推成功才记账。
        """
        pending_since = 0.0
        waiting_logged = False
        while not self._closing:
            try:
                # 托盘「设置…」的待办：注入层可能比 loaded 晚一步才就绪
                # （登录跳转、页面重绘都会重新注入），所以每轮兜一次，
                # 成功即清；失败不记账，下一轮再来。
                if self._settings_pending:
                    self._flush_settings_request()
                payload = self._state_payload()
                marker = "%s|%s|%s|%s" % (payload["state"], payload["accounts"],
                                          payload["uptime"], payload["core_version"])
                if marker != self._last_state and self.window is not None:
                    import json
                    with self._inject_lock:
                        ready = self.window.evaluate_js(
                            "typeof window.__wbShellApply === 'function'")
                        if not ready:
                            # 注入层还没就绪（页面还在加载/刚导航）：等下一轮，
                            # 不记账，所以不会把这次状态丢掉。
                            if not waiting_logged:
                                waiting_logged = True
                                pending_since = time.time()
                            self._pending_state = payload
                        else:
                            self.window.evaluate_js(
                                "window.__wbShellApply(%s)"
                                % json.dumps(payload, ensure_ascii=False))
                            self._last_state = marker
                            self._pending_state = None
                            if waiting_logged:
                                self.log("注入层就绪，状态已推送（等待 %.1f 秒）"
                                         % (time.time() - pending_since))
                                waiting_logged = False
            except Exception as exc:
                self.log("状态推送异常：%r" % (exc,), "WARN")
            time.sleep(0.5)

    def preflight(self):
        """建窗前的预检：把"不可用"变成一句能展示的原因，而不是崩溃。

        WebView2 运行时缺失/被策略禁用是企业环境里最常见的失败，pywebview
        抛出来的是底层 COM 错误，直接给用户看没有意义。
        """
        info = vendor_info = None
        try:
            import vendor as vendor_mod
            vendor_info = vendor_mod.ensure()
        except Exception as exc:
            return False, "vendor 引导失败：%s" % exc
        if not vendor_info.get("webview"):
            reasons = "；".join(vendor_info.get("errors") or []) or "未知原因"
            return False, "pywebview 不可用（%s）" % reasons
        try:
            import webview  # noqa: F401
        except Exception as exc:
            return False, "import webview 失败：%s" % exc
        if os.name == "nt":
            loader = os.path.join(paths.vendor_dir(), "py", "webview", "lib",
                                  "runtimes", "win-x64", "native",
                                  "WebView2Loader.dll")
            if not os.path.isfile(loader):
                return False, "缺少 WebView2Loader.dll（%s）" % loader
            if not self._webview2_runtime_present():
                return False, ("未检测到 Microsoft Edge WebView2 运行时，"
                               "可安装 Evergreen 运行时后重试")
        return True, ""

    @staticmethod
    def _webview2_runtime_present():
        try:
            import winreg
        except ImportError:
            return True
        key = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
               r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, key) as handle:
                    version, _ = winreg.QueryValueEx(handle, "pv")
                if version and version != "0.0.0.0":
                    return True
            except OSError:
                continue
        return False

    def _notify_tray_hint(self):
        """首次关到托盘时弹一次气泡，否则用户会以为程序退出了。"""
        coordinator = getattr(self, "coordinator", None)
        if coordinator is not None:
            coordinator.notify_tray_hint()
            return
        if self._tray_hint_shown:
            return
        self._tray_hint_shown = True
        try:
            if self.tray is not None and getattr(self.tray, "available", False):
                self.tray.notify("窗口已关闭，代理仍在后台运行",
                                 "右键托盘图标可重新打开窗口或退出")
        except Exception:
            pass

    @property
    def tray_available(self):
        return self.tray is not None and getattr(self.tray, "available", False)

    def show_window(self):
        """托盘「显示窗口」：窗口被关掉了就重建一个，还在就只唤醒。"""
        self._hidden_to_tray = False
        if self._window_open.is_set():
            try:
                if self.window is not None:
                    self.window.show()
                return
            except Exception as exc:
                self.log("显示已有窗口失败，改为重建：%r" % (exc,))
        self.log("按托盘请求重建窗口")
        self._wake.set()

    # ---- 托盘「设置…」------------------------------------------------------

    def request_settings(self):
        """把"打开设置对话框"记成待办，等注入层就绪再兑现。

        `--tray-only` 下点「设置…」时**窗口还不存在**（这一次点击要先建窗），
        所以不能在点击那一刻直接推 JS——那时既没有窗口也没有注入层。
        先记意图，`_on_loaded` / `_sync_state_loop` 负责兑现。
        """
        self._settings_pending = True

    def show_settings(self):
        """托盘「设置…」：窗口在就唤醒它并开设置；窗口没了就先重建再开。"""
        self._settings_pending = True
        if self._window_open.is_set():
            self.log("按托盘请求打开设置对话框")
        else:
            self.log("托盘请求打开设置：窗口不在，重建后打开")
        self.show_window()
        # 窗口已经开着时这里立刻就能开；刚重建的那一路注入层还没就绪，
        # 由 _on_loaded 与 _sync_state_loop 兜底，不会漏。
        self._flush_settings_request()

    def _flush_settings_request(self):
        """注入层就绪就把设置对话框推出去。返回 True 表示这次推成功了。

        就绪判定与状态推送（`_sync_state_loop`）用同一套办法：先确认
        `window.__wbShellSettings` 真的是函数再推。`evaluate_js("x && x()")`
        在注入层没装好时**不会报错**（短路成 undefined），只看有没有抛异常
        是不够的——这个坑在状态推送上已经踩过一次（见那里的注释）。
        """
        if not self._settings_pending or self.window is None:
            return False
        with self._inject_lock:
            try:
                ready = self.window.evaluate_js(
                    "typeof window.__wbShellSettings === 'function'")
            except Exception as exc:
                self.log("设置请求：注入层探测失败 %r" % (exc,))
                return False
            if not ready:
                return False
            try:
                self.window.evaluate_js("window.__wbShellSettings()")
            except Exception as exc:
                self.log("推送设置对话框失败：%r" % (exc,), "WARN")
                return False
        self._settings_pending = False
        self.log("托盘请求的设置对话框已打开")
        return True

    def force_close(self):
        """明确退出：销毁窗口并阻止重建（销毁后事件循环才会返回）。"""
        self._closing = True
        self._wake.set()
        # 退出前把窗口大小记住（值没变不写盘）
        try:
            self.save_geometry()
        except Exception as exc:
            self.log("保存窗口大小失败：%r" % (exc,))
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception as exc:
            self.log("销毁窗口失败：%r" % (exc,))

    # ---- 单轮窗口 ---------------------------------------------------------

    def _initial_size(self):
        """窗口初始宽高：优先用上次记住的（`window_geometry`），否则用默认。

        `window_geometry` 以前只有原生兜底窗口会读写（ui_native 里），
        内嵌看板窗口写死 1200×800 —— 用户调好的窗口关掉再开就变回去了。
        这里只取宽高：位置先不恢复，以免多显示器/拔掉副屏后窗口跑到屏幕外。
        """
        geometry = str((self.config or {}).get("window_geometry") or "").strip()
        if geometry:
            # 形如 "1280x800" 或 "1280x800+100+50"
            match = re.match(r"^\s*(\d{3,5})\s*[xX]\s*(\d{3,5})", geometry)
            if match:
                width = int(match.group(1))
                height = int(match.group(2))
                # 钳到合理范围：太小看不见、太大会超出屏幕
                width = max(880, min(width, 4096))
                height = max(560, min(height, 4096))
                return width, height
        return DEFAULT_SIZE

    def _remember_geometry(self, window=None):
        """把当前窗口宽高写回配置（只写内存；调用方决定何时落盘）。"""
        window = window or self.window
        if window is None:
            return ""
        try:
            width = int(getattr(window, "width", 0) or 0)
            height = int(getattr(window, "height", 0) or 0)
        except Exception:
            return ""
        if width < 400 or height < 300:
            # 没拿到真实尺寸（或被最小化了）：别把 0×0 存进去
            return ""
        geometry = "%dx%d" % (width, height)
        self.config["window_geometry"] = geometry
        return geometry

    def _build_window(self, webview, url, api):
        """建一个窗口并挂好事件。返回该窗口对象。"""
        width, height = self._initial_size()
        window = webview.create_window(
            WINDOW_TITLE, url, js_api=api, width=width, height=height,
            min_size=(880, 560), confirm_close=False)

        def _on_resized(*_args):
            # 变化即记在内存里（不每拖一下就写盘）；关窗/退出时统一保存。
            self._remember_geometry(window)

        # 入口脚本（启动.vbs）用"隐藏窗口样式"拉起本进程，这份 STARTUPINFO 会被
        # WinForms 继承，主窗口建出来就是不可见的——进程都在跑、内核也通，
        # 只是"看不见"。所以窗口 ready 后主动显示一次。
        # start_minimized（开机自启）时不显示，让它留在托盘。
        state = {"logged": False}

        def _ensure_visible():
            if self.start_minimized and not self._hidden_to_tray:
                return
            try:
                if self.window is not None:
                    self.window.show()
                    if not state["logged"]:
                        state["logged"] = True
                        self.log("已显式显示主窗口"
                                 "（避免继承自启动方的隐藏窗口样式）")
            except Exception as exc:
                self.log("显式显示窗口失败：%r" % (exc,))

        # pywebview 6.x 的 start() 没有 inject_js（旧版 API），注入走
        # _watch_loop + window.evaluate_js；loaded 到了再补一次防竞态。
        def _on_loaded():
            self._inject_bar()
            _ensure_visible()
            # 托盘「设置…」是在窗口建出来**之前**点的（tray-only 模式）：
            # 注入层一就绪就把设置对话框打开，别让那一次点击落空。
            self._flush_settings_request()

        def _on_closed():
            self._window_open.clear()
            # 窗口关掉了，这个待办就作废：否则它会在**下一次**（可能与设置
            # 毫不相干的）建窗时突然弹出设置对话框——那才是"自己冒出来"的 bug。
            self._settings_pending = False
            self._remember_geometry(window)
            self.log("窗口已关闭（代理仍在运行，可从托盘重新打开）")

        for name, handler in (("shown", _ensure_visible),
                              ("loaded", _on_loaded),
                              ("closed", _on_closed),
                              ("resized", _on_resized)):
            try:
                event = getattr(window.events, name)
                event += handler
            except Exception as exc:
                self.log("注册 %s 事件失败：%r" % (name, exc))

        # 刻意**不**注册 closing 事件：pywebview 6.2.1 的 Event.set() 在子线程里
        # 收集处理函数返回值、却立刻读取，集合永远为空，取消关闭不可能生效
        # （tests/probe_closing.py 三种写法实测都无效）。既然拦不住，就别假装能拦：
        # × 就是"关窗口"——窗口销毁、事件循环返回，由 run() 决定重建还是退出。
        return window

    def save_geometry(self):
        """把记住的窗口宽高落盘到 `shell.json`（值没变就不写）。

        与局域网开关同一套纪律：写前留一份 config-backups、值没变不写盘，
        免得"我只是关了个窗口"却把配置文件的 mtime 改了。
        返回 (ok, message)；没东西可存时返回 (True, "") 且不写盘。
        """
        geometry = str(self.config.get("window_geometry") or "").strip()
        if not geometry:
            return True, ""
        try:
            disk = config_mod.load(self.shell)["config"]
        except Exception:
            disk = {}
        if str(disk.get("window_geometry") or "").strip() == geometry:
            return True, ""
        try:
            config_mod.backup_before_overwrite(self.shell)
        except Exception:
            pass
        ok, message = config_mod.save(self.config, self.shell)
        if ok:
            self.log("窗口大小已记住：%s" % geometry)
        else:
            self.log("窗口大小写盘失败：%s" % message, "WARN")
        return ok, message

    def run(self):
        """窗口生命周期循环。

        × 关掉窗口 → `webview.start()` 返回 → 这里判断用户是不是要退出。
        不是（点的是 ×）就留在托盘，等托盘的「显示窗口」再重建一个。
        """
        ok, reason = self.preflight()
        if not ok:
            raise RuntimeError(reason)

        import webview

        url = self.service.status()["dashboard"]
        self.log("打开内嵌看板：%s（会话目录 %s）" % (url, self._storage))
        os.makedirs(self._storage, exist_ok=True)

        class Api(object):
            pass

        api = Api()
        api.get_state = self.api_get_state
        api.get_patches = self.api_get_patches
        api.action = self.api_action
        api.open_dashboard = self.api_open_dashboard
        api.quit = self.api_quit
        api.kernel_info = self.api_kernel_info
        api.kernel_validate = self.api_kernel_validate
        api.kernel_set = self.api_kernel_set
        api.panel_status = self.api_panel_status
        api.lan_info = self.api_lan_info
        api.lan_set = self.api_lan_set
        api.copy = self.api_copy
        api.panel_secret = self.api_panel_secret
        api.panel_remember_password = self.api_panel_remember_password
        api.restart_shell = self.api_restart_shell
        api.reload_dashboard = self.api_reload_dashboard
        api.update_check = self.api_update_check
        api.update_install = self.api_update_install
        api.watchdog_get = self.api_watchdog_get
        api.watchdog_set = self.api_watchdog_set

        threading.Thread(target=self._watch_loop, daemon=True,
                         name="wb-web-inject").start()
        threading.Thread(target=self._sync_state_loop, daemon=True,
                         name="wb-web-state").start()

        rounds = 0
        while not self._closing:
            rounds += 1
            self._wake.clear()
            self.window = self._build_window(webview, url, api)
            self._window_open.set()
            self.log("进入窗口事件循环（第 %d 轮）" % rounds)
            try:
                webview.start(debug=False, storage_path=self._storage,
                              private_mode=False, gui="edgechromium")
            except Exception as exc:
                self.log("窗口事件循环异常：%r" % (exc,))
                return 1
            self._window_open.clear()
            if self._closing:
                break
            # 用户改了「内核位置」、或点了「重启外壳」：不要在这里重建窗口，
            # 交给外层（main 的 finally）拉起新外壳。
            coordinator = getattr(self, "coordinator", None)
            if coordinator is not None and getattr(coordinator,
                                                   "wants_relaunch", False):
                self.log("外壳即将重启（内核位置变更或用户要求），退出窗口循环")
                break

            coordinator = getattr(self, "coordinator", None)
            has_tray = self.tray_available or (
                coordinator is not None and coordinator.tray_available)
            if has_tray and self.config.get("close_to_tray", True):
                # 正常路径：× = 只关窗口。内核与外壳都活着，等托盘召唤。
                self.log("窗口已关闭，外壳留在托盘，内核继续运行")
                self._notify_tray_hint()
                # `_wake.clear()` 在每轮开头做过，但托盘请求可能在"窗口刚关到
                # 这里"之间就置位了——那样下面一 wait 就会把信号吞掉、白等一轮。
                # 所以这里先非阻塞看一眼，拿到就立刻重建。
                if self._wake.is_set():
                    self._wake.clear()
                    self.log("窗口关闭前就收到了显示请求，立即重建")
                    continue
                # 等托盘点「显示窗口」。**超时绝不能自己重建**：
                # `Event.wait()` 超时也会返回，早先这里直接 continue，于是变成
                # "关掉窗口 10 分钟后自己弹回来"——2026-10-14 实测：21:46:59 关窗、
                # 21:56:59 自己开窗，正好 600 秒，日志里那轮**没有**任何托盘请求。
                # 现在超时只是再等一轮，窗口只在你明确要它的时候出现。
                while not self._closing and not self._wake.is_set():
                    self._wake.wait(self.hidden_wait)
                if self._closing:
                    break
                self._wake.clear()
                self.log("托盘请求显示窗口：重建窗口")
                continue

            # 没有托盘：窗口关了就没法再打开，所以问一句。
            if coordinator is not None:
                if coordinator.confirm_if_needed("内嵌窗口已关闭"):
                    self._closing = True
                    break
                self.log("用户选择只关窗口：内核继续运行，外壳退出")
                break
            break

        self.log("窗口生命周期结束（共 %d 轮）" % rounds)
        try:
            self.save_geometry()
        except Exception as exc:
            self.log("保存窗口大小失败：%r" % (exc,))
        return 0
