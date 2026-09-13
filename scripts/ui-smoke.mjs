import { writeFileSync } from "node:fs";

const outputPath = process.argv[2];
const email = process.env.CMH_SMOKE_EMAIL;
const password = process.env.CMH_SMOKE_PASSWORD;

if (!outputPath || !email || !password) {
  throw new Error("Usage: set CMH_SMOKE_EMAIL/CMH_SMOKE_PASSWORD and pass an output PNG path");
}

const tabs = await fetch("http://127.0.0.1:9222/json").then((response) => response.json());
const page = tabs.find((tab) => tab.type === "page");
if (!page?.webSocketDebuggerUrl) throw new Error("No debuggable browser page was found");

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let nextId = 1;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id) return;
  const callback = pending.get(message.id);
  if (!callback) return;
  pending.delete(message.id);
  if (message.error) callback.reject(new Error(message.error.message));
  else callback.resolve(message.result);
});

function command(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

await command("Page.enable");
await command("Runtime.enable");
await command("Emulation.setDeviceMetricsOverride", {
  width: 1440,
  height: 1000,
  deviceScaleFactor: 1,
  mobile: false,
});
await command("Page.navigate", { url: "http://localhost:3000/" });
await delay(1800);

const login = await command("Runtime.evaluate", {
  expression: `(async () => {
    const response = await fetch('/api/v1/auth/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(${JSON.stringify({ email, password })})
    });
    return response.status;
  })()`,
  awaitPromise: true,
  returnByValue: true,
});
if (login.result.value !== 200) throw new Error(`Local login returned ${login.result.value}`);

await command("Page.reload", { ignoreCache: true });
await delay(2200);
await command("Runtime.evaluate", {
  expression: `localStorage.setItem('cmh-locale', 'fa'); localStorage.setItem('cmh-theme', 'light'); location.reload();`,
});
await delay(2200);

const ownerShell = await command("Runtime.evaluate", {
  expression: `(() => ({
    items: [...document.querySelectorAll('.sidebar-nav .nav-item')].map((item) => item.textContent.trim()),
    hasTenantSelector: document.querySelector('.tenant-select') !== null,
    hasWalletBalance: document.querySelector('.wallet-balance') !== null,
  }))()`,
  returnByValue: true,
});
const expectedOwnerItems = ["کسب‌وکارها", "کاربران کسب‌وکار", "تنظیمات"];
if (JSON.stringify(ownerShell.result.value.items) !== JSON.stringify(expectedOwnerItems)) {
  throw new Error(`Unexpected Owner navigation: ${ownerShell.result.value.items.join(", ")}`);
}
if (ownerShell.result.value.hasTenantSelector || ownerShell.result.value.hasWalletBalance) {
  throw new Error("Business-only header controls are visible to the platform Owner");
}
console.log(`owner-navigation=${ownerShell.result.value.items.join(" | ")}`);

const navigation = await command("Runtime.evaluate", {
  expression: `(() => {
    const button = [...document.querySelectorAll('button')].find((item) => item.textContent.includes('کسب‌وکارها'));
    if (!button) return false;
    button.click();
    return true;
  })()`,
  returnByValue: true,
});
if (!navigation.result.value) throw new Error("Businesses navigation item was not rendered");
await delay(1500);

const fontCheck = await command("Runtime.evaluate", {
  expression: `(async () => {
    await document.fonts.ready;
    return {
      family: getComputedStyle(document.body).fontFamily,
      loaded: document.fonts.check('16px vazirmatn'),
    };
  })()`,
  awaitPromise: true,
  returnByValue: true,
});
const { family: fontFamily, loaded: fontLoaded } = fontCheck.result.value;
if (!fontLoaded || !fontFamily.toLowerCase().includes("vazirmatn")) {
  throw new Error(`Vazirmatn is not active: ${fontFamily}`);
}
console.log(`fa-font-family=${fontFamily}`);

const screenshot = await command("Page.captureScreenshot", {
  format: "png",
  captureBeyondViewport: true,
});
writeFileSync(outputPath, Buffer.from(screenshot.data, "base64"));

await command("Runtime.evaluate", {
  expression: `localStorage.setItem('cmh-locale', 'en'); location.reload();`,
});
await delay(1800);
const englishFontCheck = await command("Runtime.evaluate", {
  expression: `(async () => {
    await document.fonts.ready;
    return {
      family: getComputedStyle(document.body).fontFamily,
      loaded: document.fonts.check('16px vazirmatn'),
    };
  })()`,
  awaitPromise: true,
  returnByValue: true,
});
const { family: englishFontFamily, loaded: englishFontLoaded } = englishFontCheck.result.value;
if (!englishFontLoaded || !englishFontFamily.toLowerCase().includes("vazirmatn")) {
  throw new Error(`Vazirmatn is not active in English: ${englishFontFamily}`);
}
console.log(`en-font-family=${englishFontFamily}`);
socket.close();
