// Brawl Insights のフロントエンドをヘッドレスChromiumで開き、スクリーンショットとconsoleエラーを取得する。
// Usage:
//   node shot.js <path> [output.png] [options]
// Options (env vars):
//   BASE_URL      デフォルト http://localhost:8000
//   THEME         'light' | 'dark' | 'auto' (デフォルト auto)
//   USERNAME      ログインするユーザー名 (指定時のみログイン処理を実行)
//   PASSWORD      ログインするパスワード
//   VIEWPORT      'desktop' | 'mobile' (デフォルト desktop)
//   FULL_PAGE     '1' でページ全体をスクリーンショット
//
// 例:
//   BASE_URL=http://localhost:8000 node shot.js /ja/ top.png
//   THEME=dark node shot.js /en/stats top_dark.png
//   USERNAME=こうすけ PASSWORD=12345678 node shot.js /ja/account account.png

const { chromium } = require('playwright');
const path = require('path');

const [, , targetPath, outputArg] = process.argv;
if (!targetPath) {
  console.error('Usage: node shot.js <path> [output.png]');
  process.exit(1);
}

const BASE_URL = process.env.BASE_URL || 'http://localhost:8000';
const THEME = process.env.THEME || 'auto';
const VIEWPORT = process.env.VIEWPORT === 'mobile'
  ? { width: 390, height: 844 }
  : { width: 1280, height: 900 };
const FULL_PAGE = process.env.FULL_PAGE === '1';
const output = path.resolve(outputArg || 'screenshot.png');

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: VIEWPORT });

  // base.html は localStorage の 'brawlInsightsTheme' ('auto'|'dark'|'light') を見て配色を決める
  await context.addInitScript((theme) => {
    localStorage.setItem('brawlInsightsTheme', theme);
  }, THEME);

  const page = await context.newPage();
  const consoleErrors = [];
  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
  page.on('pageerror', (err) => consoleErrors.push(err.message));

  if (process.env.USERNAME && process.env.PASSWORD) {
    const lang = targetPath.startsWith('/en') ? 'en' : 'ja';
    await page.goto(`${BASE_URL}/${lang}/login`, { waitUntil: 'networkidle' });
    await page.fill('input[name="username"]', process.env.USERNAME);
    await page.fill('input[name="password"]', process.env.PASSWORD);
    await page.click('form.auth-form button[type="submit"]');
    await page.waitForLoadState('networkidle');
  }

  const resp = await page.goto(`${BASE_URL}${targetPath}`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.screenshot({ path: output, fullPage: FULL_PAGE });

  console.log('status:', resp ? resp.status() : 'no response');
  console.log('title:', await page.title());
  console.log('screenshot:', output);
  console.log('console errors:', JSON.stringify(consoleErrors));

  await browser.close();
  if (consoleErrors.length > 0) process.exitCode = 1;
})();
