/*
 * ボックスシミュレーターの開封動画について、余白のぼかし背景用の色(動画全体を通した平均色を COLS x ROWS マスで)を事前計算する。
 * iOS/iPadOS(WebKit)では再生中の動画をcanvasに描画すると動画自体が表示されなくなるため、iOSではこの色で背景を描く。
 * 動画を追加・差し替えたら、開発サーバーを起動した状態で実行し直すこと。
 *
 *   node scripts/generate_box_video_backdrops.js
 *   BASE_URL=http://localhost:8000 node scripts/generate_box_video_backdrops.js
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const VIDEO_DIR = path.join(ROOT, 'videos');
const OUTPUT_PATH = path.join(ROOT, 'app', 'static', 'data', 'box_video_backdrops.json');
const BASE_URL = process.env.BASE_URL || 'http://localhost:8000';
const COLS = 4;
const ROWS = 3;
const SAMPLE_COUNT = 12;

(async () => {
    const files = fs.readdirSync(VIDEO_DIR).filter((name) => name.toLowerCase().endsWith('.mp4')).sort();
    const browser = await chromium.launch();
    const page = await browser.newPage();
    // 同一オリジンにしないとcanvasの画素を読めないため、開発サーバー上のページで動画を読み込む
    await page.goto(`${BASE_URL}/static/offline.html`);

    const videos = {};
    for (const name of files) {
        const src = `${BASE_URL}/videos/${encodeURIComponent(name)}`;
        const colors = await page.evaluate(async ({ src, cols, rows, sampleCount }) => {
            const video = document.createElement('video');
            video.muted = true;
            video.preload = 'auto';
            video.src = src;
            await new Promise((resolve, reject) => {
                video.addEventListener('loadeddata', resolve, { once: true });
                video.addEventListener('error', () => reject(new Error(`failed to load ${src}`)), { once: true });
            });
            const cell = 16;
            const canvas = document.createElement('canvas');
            canvas.width = cols * cell;
            canvas.height = rows * cell;
            const context = canvas.getContext('2d', { willReadFrequently: true });
            const sums = new Array(cols * rows * 3).fill(0);
            for (let s = 0; s < sampleCount; s++) {
                const time = video.duration * (s + 0.5) / sampleCount;
                await new Promise((resolve) => {
                    video.addEventListener('seeked', resolve, { once: true });
                    video.currentTime = time;
                });
                context.drawImage(video, 0, 0, canvas.width, canvas.height);
                const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
                for (let y = 0; y < canvas.height; y++) {
                    for (let x = 0; x < canvas.width; x++) {
                        const index = (Math.floor(y / cell) * cols + Math.floor(x / cell)) * 3;
                        const i = (y * canvas.width + x) * 4;
                        for (let c = 0; c < 3; c++) sums[index + c] += data[i + c];
                    }
                }
            }
            const perCell = cell * cell * sampleCount;
            let hex = '';
            for (const value of sums) {
                hex += Math.round(value / perCell).toString(16).padStart(2, '0');
            }
            return hex;
        }, { src, cols: COLS, rows: ROWS, sampleCount: SAMPLE_COUNT });
        videos[name] = colors;
        console.log(`${name}: ${colors}`);
    }
    await browser.close();

    fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
    // colors: 左上から行ごとに並べた COLS x ROWS 個の RRGGBB を連結したもの
    fs.writeFileSync(OUTPUT_PATH, `${JSON.stringify({ cols: COLS, rows: ROWS, videos }, null, 1)}\n`);
    console.log(`wrote ${Object.keys(videos).length} videos to ${path.relative(ROOT, OUTPUT_PATH)}`);
})();
