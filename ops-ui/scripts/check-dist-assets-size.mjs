/**
 * 验收：dist/assets 下各文件 gzip 体积之和 < 150KB（与 Vite 报告口径一致，便于嵌入）。
 * 同时在控制台打印未压缩总字节供参考。
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..", "dist", "assets");
const maxGzipBytes = 150 * 1024;

let rawTotal = 0;
let gzipTotal = 0;
const rows = [];

try {
  statSync(root);
} catch {
  console.error("dist/assets 不存在，请先执行 vite build");
  process.exit(1);
}

for (const name of readdirSync(root)) {
  const p = join(root, name);
  if (!statSync(p).isFile()) continue;
  const buf = readFileSync(p);
  rawTotal += buf.length;
  const gz = gzipSync(buf);
  gzipTotal += gz.length;
  rows.push({ name, raw: buf.length, gzip: gz.length });
}

rows.sort((a, b) => b.gzip - a.gzip);
console.log("dist/assets 体积统计:");
for (const r of rows) {
  console.log(`  ${r.name}  raw=${r.raw}  gzip=${r.gzip}`);
}
console.log(`合计 raw=${rawTotal}  gzip=${gzipTotal}  (上限 gzip ${maxGzipBytes})`);

if (gzipTotal > maxGzipBytes) {
  console.error(`失败：gzip 合计 ${gzipTotal} > ${maxGzipBytes}。可进一步 external React 或压缩依赖。`);
  process.exit(1);
}

process.exit(0);
