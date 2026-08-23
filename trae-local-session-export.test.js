const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { locateRustAdapter, publishExport, validateSessionId } = require('./trae-local-session-export');

// Catches version drift that would make the debugger pause in the wrong service.
const prefix = 'x'.repeat(17);
const source = `${prefix}class W{async getClient(){return this._client} buildRequest(){return "list_chat_sessions"} createAdapter(){}}`;
assert.deepEqual(locateRustAdapter(source), { lineNumber: 0, columnNumber: 17 });

const installedDist = '/Applications/TRAE SOLO CN.app/Contents/Resources/app/node_modules/@byted-icube/solo-lite/dist';
if (fs.existsSync(installedDist)) {
  const installedAdapter = fs.readdirSync(installedDist)
    .filter(name => name.endsWith('.mjs'))
    .some(name => {
      try {
        locateRustAdapter(fs.readFileSync(path.join(installedDist, name), 'utf8'));
        return true;
      } catch (_) {
        return false;
      }
    });
  assert.equal(installedAdapter, true, '当前安装的 Trae 已发生适配器版本漂移');
}

// Catches accidental acceptance of paths or arbitrary strings as database IDs.
assert.equal(validateSessionId('6a85e47c4fc77137878c03d0'), '6a85e47c4fc77137878c03d0');
assert.throws(() => validateSessionId('../database.db'), /会话 ID/);

// Sensitive exports are private and never overwrite an existing destination.
const testDir = fs.mkdtempSync(path.join(os.tmpdir(), 'trae-export-test-'));
try {
  const temporary = path.join(testDir, 'temporary.md');
  const target = path.join(testDir, 'target.md');
  fs.writeFileSync(temporary, '# session');
  assert.equal(publishExport(temporary, target).bytes, 9);
  assert.equal(fs.statSync(target).mode & 0o777, 0o600);
  assert.throws(() => publishExport(temporary, target), /EEXIST/);
  fs.writeFileSync(temporary, '');
  assert.throws(() => publishExport(temporary, path.join(testDir, 'empty.md')), /有效/);
} finally {
  fs.rmSync(testDir, { recursive: true, force: true });
}

console.log('OK');
