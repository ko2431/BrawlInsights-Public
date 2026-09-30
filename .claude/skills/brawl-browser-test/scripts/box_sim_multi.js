// ボックスシミュレーターの複数個開封を操作し、各段階の表示領域をスクリーンショットする。
// Usage: node box_sim_multi.js <boxKey> <count> <outDir> [taps]
// Options (env vars): BASE_URL, LANG_CODE(ja|en), THEME(light|dark), VIEWPORT(desktop|mobile)
const { chromium } = require('playwright');
const path = require('path');

const [, , boxKey, countArg, outDir, tapsArg] = process.argv;
const BASE_URL = process.env.BASE_URL || 'http://localhost:8000';
const LANG = process.env.LANG_CODE || 'ja';
const THEME = process.env.THEME || 'light';
const VIEWPORT = process.env.VIEWPORT === 'mobile' ? { width: 390, height: 844 } : { width: 1280, height: 900 };
const taps = Number(tapsArg || 6);

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: VIEWPORT, hasTouch: process.env.VIEWPORT === 'mobile' });
  await context.addInitScript((theme) => localStorage.setItem('brawlInsightsTheme', theme), THEME);
  const page = await context.newPage();
  const errors = [];
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('pageerror', (err) => errors.push(err.message));
  await page.goto(`${BASE_URL}/${LANG}/tools/box_simulator?box=${boxKey}`, { waitUntil: 'networkidle' });
  const stage = page.locator('.box-sim-stage');
  await stage.scrollIntoViewIfNeeded();
  const select = page.locator('.box-sim-count--floating select');
  if (await select.isVisible()) await select.selectOption(String(countArg));
  const shot = async (name) => stage.screenshot({ path: path.join(outDir, `${boxKey}_${name}.png`) });
  await shot('0_idle');
  await page.locator('.box-sim-reveal__card--intro').click();
  await page.waitForTimeout(1200);
  await shot('1_start');
  for (let i = 1; i <= taps; i++) {
    await page.locator('.box-sim-stage__hint').evaluate(() => {});
    await page.mouse.click(...(await stage.boundingBox().then((b) => [b.x + 8, b.y + 8])));
    await page.waitForTimeout(700);
    await shot(`2_tap${i}`);
  }
  const phase = await page.evaluate(() => Alpine.$data(document.querySelector('.tools-page__container')).phase);
  console.log('phase:', phase, 'errors:', JSON.stringify(errors));
  await browser.close();
})();
