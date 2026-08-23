#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const TRAE_BIN = '/Applications/TRAE SOLO CN.app/Contents/MacOS/Electron';
const SESSION_ID_RE = /^[0-9a-f]{24}$/;
let activeChild = null;

function validateSessionId(value) {
  if (!SESSION_ID_RE.test(value || '')) throw new Error('会话 ID 必须是 24 位小写十六进制');
  return value;
}

function locateRustAdapter(source) {
  const pattern = /class [A-Za-z_$][\w$]*\{async getClient\(\)\{return this\._client/g;
  for (const match of source.matchAll(pattern)) {
    const nearby = source.slice(Math.max(0, match.index - 30000), match.index + 30000);
    if (!nearby.includes('"list_chat_sessions"') || !nearby.includes('createAdapter(){')) continue;
    const lineStart = source.lastIndexOf('\n', match.index) + 1;
    return {
      lineNumber: source.slice(0, lineStart).split('\n').length - 1,
      columnNumber: match.index - lineStart,
    };
  }
  throw new Error('当前 Trae 版本未找到本地会话适配器');
}

function createPipeClient(child) {
  let buffer = '';
  let nextId = 0;
  let closedError = null;
  const pending = new Map();
  const listeners = new Map();

  function fail(error) {
    if (closedError) return;
    closedError = error instanceof Error ? error : new Error(String(error));
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(closedError);
    }
    pending.clear();
  }

  child.stdio[4].setEncoding('utf8');
  child.stdio[4].on('data', chunk => {
    try {
      buffer += chunk;
      let separator;
      while ((separator = buffer.indexOf('\0')) >= 0) {
        const raw = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 1);
        if (!raw) continue;
        const message = JSON.parse(raw);
        if (message.id && pending.has(message.id)) {
          const request = pending.get(message.id);
          pending.delete(message.id);
          clearTimeout(request.timer);
          if (message.error) request.reject(new Error(message.error.message));
          else request.resolve(message.result);
          continue;
        }
        for (const listener of listeners.get(message.method) || []) {
          Promise.resolve(listener(message)).catch(fail);
        }
      }
    } catch (error) {
      fail(error);
    }
  });
  child.once('error', fail);
  child.once('exit', (code, signal) => fail(new Error(`Trae 已退出 (${signal || code})`)));
  child.stdio[3].on('error', fail);
  child.stdio[4].on('error', fail);
  child.stdio[4].on('close', () => fail(new Error('Trae 调试管道已关闭')));

  return {
    send(method, params = {}, sessionId) {
      return new Promise((resolve, reject) => {
        if (closedError) return reject(closedError);
        const id = ++nextId;
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`${method} 超时`));
        }, 30000);
        pending.set(id, { resolve, reject, timer });
        child.stdio[3].write(
          JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + '\0',
          error => error && fail(error),
        );
      });
    },
    notify(method, params = {}, sessionId) {
      if (closedError) return;
      child.stdio[3].write(JSON.stringify({
        id: ++nextId,
        method,
        params,
        ...(sessionId ? { sessionId } : {}),
      }) + '\0', error => error && fail(error));
    },
    on(method, listener) {
      const values = listeners.get(method) || [];
      values.push(listener);
      listeners.set(method, values);
      return () => listeners.set(method, values.filter(value => value !== listener));
    },
  };
}

async function waitFor(check, message, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(message);
}

async function withTimeout(promise, message, timeoutMs) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function childExited(child) {
  return child.exitCode !== null || child.signalCode !== null;
}

function signalChild(child, signal) {
  if (childExited(child)) return;
  try {
    process.kill(-child.pid, signal);
  } catch (error) {
    if (error.code !== 'ESRCH') throw error;
  }
}

function waitChildExit(child, timeoutMs) {
  if (childExited(child)) return Promise.resolve(true);
  return new Promise(resolve => {
    const onExit = () => finish(true);
    const timer = setTimeout(() => finish(false), timeoutMs);
    function finish(exited) {
      clearTimeout(timer);
      child.off('exit', onExit);
      resolve(exited);
    }
    child.once('exit', onExit);
  });
}

async function closeChild(client, child) {
  if (childExited(child)) return;
  client.notify('Browser.close');
  if (await waitChildExit(child, 3000)) return;
  signalChild(child, 'SIGTERM');
  if (await waitChildExit(child, 3000)) return;
  signalChild(child, 'SIGKILL');
  if (!await waitChildExit(child, 3000)) throw new Error('无法关闭本次启动的 Trae 进程');
}

function publishExport(temporaryPath, target) {
  const stat = fs.lstatSync(temporaryPath);
  if (!stat.isFile() || stat.size === 0) throw new Error('Trae 未生成有效的导出文件');
  fs.chmodSync(temporaryPath, 0o600);
  fs.linkSync(temporaryPath, target);
  return { path: target, bytes: stat.size };
}

async function exportSession(sessionId, outputPath) {
  validateSessionId(sessionId);
  const target = path.resolve(outputPath);
  if (fs.existsSync(target)) throw new Error(`目标文件已存在: ${target}`);
  const targetDir = path.dirname(target);
  if (!fs.existsSync(targetDir) || !fs.statSync(targetDir).isDirectory()) {
    throw new Error(`目标目录不存在: ${targetDir}`);
  }
  if (!fs.existsSync(TRAE_BIN)) throw new Error(`未找到 Trae: ${TRAE_BIN}`);
  if (spawnSync('/usr/bin/pgrep', ['-f', TRAE_BIN]).status === 0) {
    throw new Error('请先退出 TRAE SOLO CN，导出器不会接管正在运行的实例');
  }

  const temporaryDir = fs.mkdtempSync(path.join(targetDir, '.trae-export-'));
  const temporaryPath = path.join(temporaryDir, 'session.md');
  const child = spawn(TRAE_BIN, ['--remote-debugging-pipe'], {
    stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'],
    detached: true,
  });
  activeChild = child;
  const client = createPipeClient(child);

  try {
    await client.send('Browser.getVersion');
    const page = await waitFor(async () => {
      const { targetInfos = [] } = await client.send('Target.getTargets');
      return targetInfos.find(item => item.type === 'page' && item.url.includes('/solo/solo-lite.html'));
    }, 'Trae 页面启动超时');
    const { sessionId: debuggerSession } = await client.send('Target.attachToTarget', {
      targetId: page.targetId,
      flatten: true,
    });

    const scripts = new Map();
    client.on('Debugger.scriptParsed', message => {
      if (message.sessionId === debuggerSession) scripts.set(message.params.scriptId, message.params.url);
    });
    await client.send('Page.enable', {}, debuggerSession);
    await client.send('Runtime.enable', {}, debuggerSession);
    await client.send('Debugger.enable', {}, debuggerSession);

    const adapter = await waitFor(async () => {
      for (const [scriptId, url] of scripts) {
        if (!url.includes('/@byted-icube/solo-lite/dist/') || !url.endsWith('.mjs')) continue;
        const { scriptSource } = await client.send('Debugger.getScriptSource', { scriptId }, debuggerSession);
        try {
          return { scriptId, url, ...locateRustAdapter(scriptSource) };
        } catch (_) {
          // 继续检查下一个已加载 chunk。
        }
      }
      return null;
    }, '未加载 Trae 本地会话模块');

    const { locations } = await client.send('Debugger.getPossibleBreakpoints', {
      start: { scriptId: adapter.scriptId, lineNumber: adapter.lineNumber, columnNumber: adapter.columnNumber },
      end: { scriptId: adapter.scriptId, lineNumber: adapter.lineNumber, columnNumber: adapter.columnNumber + 300 },
      restrictToFunction: false,
    }, debuggerSession);
    if (!locations.length) throw new Error('本地会话适配器没有可用断点');

    let resolveCapture;
    let rejectCapture;
    const captured = new Promise((resolve, reject) => {
      resolveCapture = resolve;
      rejectCapture = reject;
    });
    const stopPaused = client.on('Debugger.paused', async message => {
      if (message.sessionId !== debuggerSession) return;
      try {
        const frame = message.params.callFrames[0];
        const evaluated = await client.send('Debugger.evaluateOnCallFrame', {
          callFrameId: frame.callFrameId,
          expression: 'globalThis.__harborTraeRustAdapter=this; Object.getOwnPropertyNames(Object.getPrototypeOf(this))',
          returnByValue: true,
        }, debuggerSession);
        const names = evaluated.result.value || [];
        if (names.includes('createAdapter') && names.includes('buildRequest')) resolveCapture();
      } catch (error) {
        rejectCapture(error);
      } finally {
        client.send('Debugger.resume', {}, debuggerSession).catch(rejectCapture);
      }
    });
    const { breakpointId } = await client.send('Debugger.setBreakpointByUrl', {
      url: adapter.url,
      lineNumber: locations[0].lineNumber,
      columnNumber: locations[0].columnNumber,
    }, debuggerSession);
    await client.send('Page.reload', {}, debuggerSession);
    await withTimeout(captured, '捕获本地会话服务超时', 20000);
    stopPaused();
    await client.send('Debugger.removeBreakpoint', { breakpointId }, debuggerSession);

    const expression = `(async()=>{const client=await __harborTraeRustAdapter.getClient();return client.request({service:'lite',method:'export_past_chat',data:{session_id:${JSON.stringify(sessionId)},export_path:${JSON.stringify(temporaryPath)},header_extra:''},context:{}})})()`;
    const result = await client.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    }, debuggerSession);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    const response = result.result.value;
    if (response?.code !== 0) throw new Error(response?.message || 'Trae 导出失败');
    if (!fs.existsSync(temporaryPath)) throw new Error('Trae 返回成功但未生成导出文件');
    return publishExport(temporaryPath, target);
  } finally {
    try {
      await closeChild(client, child);
    } finally {
      if (activeChild === child) activeChild = null;
      fs.rmSync(temporaryDir, { recursive: true, force: true });
    }
  }
}

async function main() {
  const [, , sessionId, outputPath] = process.argv;
  if (!sessionId || !outputPath) throw new Error('用法: node trae-local-session-export.js <24位会话ID> <输出.md>');
  let interrupted = null;
  const onSignal = signal => {
    interrupted ||= signal;
    if (activeChild) signalChild(activeChild, 'SIGTERM');
  };
  process.once('SIGINT', onSignal);
  process.once('SIGTERM', onSignal);
  try {
    const result = await exportSession(sessionId, outputPath);
    if (interrupted) throw new Error(`导出已被 ${interrupted} 中止`);
    console.log(`导出成功: ${result.path} (${result.bytes} bytes)`);
  } finally {
    process.off('SIGINT', onSignal);
    process.off('SIGTERM', onSignal);
  }
}

module.exports = { exportSession, locateRustAdapter, publishExport, validateSessionId };

if (require.main === module) main().catch(error => {
  console.error(`错误: ${error.message}`);
  process.exitCode = 1;
});
