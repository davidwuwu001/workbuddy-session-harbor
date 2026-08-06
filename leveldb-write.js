// leveldb-write.js — 写入 Local Storage leveldb 的指定 key
// 用法: node leveldb-write.js <dbPath> <key> <value>
// 依赖: classic-level (NODE_PATH 指向 workspace/node_modules)
const { Level } = require('classic-level');

const [,, dbPath, key, value] = process.argv;
if (!dbPath || !key) {
  console.error('用法: node leveldb-write.js <dbPath> <key> <value>');
  process.exit(1);
}

(async () => {
  let db;
  try {
    db = new Level(dbPath, { valueEncoding: 'utf8' });
    await db.put(key, value || '');
    console.log('OK: ' + key + ' written (' + (value || '').length + ' chars)');
    await db.close();
  } catch (e) {
    console.error('ERROR:', e.message);
    if (db) { try { await db.close(); } catch (_) {} }
    process.exit(1);
  }
})();
