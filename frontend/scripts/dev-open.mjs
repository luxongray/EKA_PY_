// dev 自启浏览器：起 next dev 后轮询 5173 端口，通了就用系统默认浏览器打开
import { spawn, exec } from "node:child_process";
import http from "node:http";

const PORT = 5173;
const URL = `http://localhost:${PORT}`;

// 子进程继承 stdio，日志照常输出，Ctrl+C 一起退出
const child = spawn("pnpm", ["next", "dev", "-p", String(PORT)], {
  stdio: "inherit",
  shell: true,
});
child.on("exit", (code) => process.exit(code ?? 0));

// 轮询端口：通了就打开浏览器，只开一次
let opened = false;
const timer = setInterval(() => {
  http
    .get(URL, (res) => {
      res.resume();
      if (!opened) {
        opened = true;
        clearInterval(timer);
        exec(`start "" "${URL}"`);
      }
    })
    .on("error", () => {
      // 端口未就绪，继续等
    });
}, 500);
