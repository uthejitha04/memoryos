// MemoryOS desktop shell: starts the Python core (if not already running)
// and wraps the glass UI in a native window.
const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const PORT = 8090;
const URL = `http://localhost:${PORT}`;
const ROOT = path.join(__dirname, "..");
const PYTHON = path.join(ROOT, ".venv", "Scripts", "python.exe");

let serverProc = null;
let weStartedServer = false;

function serverUp() {
  return new Promise(resolve => {
    const req = http.get(`${URL}/status`, res => resolve(res.statusCode === 200));
    req.on("error", () => resolve(false));
    req.setTimeout(1500, () => { req.destroy(); resolve(false); });
  });
}

async function ensureServer() {
  if (await serverUp()) return;                 // reuse a start.bat instance
  serverProc = spawn(PYTHON, [path.join(ROOT, "server.py")], {
    cwd: ROOT, stdio: "ignore", windowsHide: true,
  });
  weStartedServer = true;
  for (let i = 0; i < 120; i++) {               // model load can take a while
    if (await serverUp()) return;
    await new Promise(r => setTimeout(r, 1000));
  }
  throw new Error("MemoryOS core failed to start");
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 800,
    minWidth: 420,
    minHeight: 600,
    backgroundColor: "#0a0c12",
    autoHideMenuBar: true,
    title: "MemoryOS",
    icon: path.join(ROOT, "static", "icon.svg"),
    webPreferences: { contextIsolation: true },
  });
  // external links open in the real browser, not the app shell
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  win.loadURL(URL);
}

app.whenReady().then(async () => {
  try {
    await ensureServer();
    createWindow();
  } catch (e) {
    const { dialog } = require("electron");
    dialog.showErrorBox("MemoryOS", String(e));
    app.quit();
  }
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (weStartedServer && serverProc) serverProc.kill();
  app.quit();
});
