// AdSense の手動の広告ユニット（components/adsense_unit.html）を、画面に近づいたときに1枠ずつ読み込む。
// adsbygoogle.push({}) は「ページの中でまだ読み込んでいない最初の ins.adsbygoogle」を埋めるので、
// 画面に近づいた枠（隠れたタブの中は幅が0なので対象外）にだけ class を付けてから読み込む。
// あとから差し込んだ枠（掲示板の一覧・「さらに表示」など）は、差し込んだ側が window.biScanAdUnits(root) を呼ぶ。
// アプリ版では出さない（アプリは AdMob の下部バナーだけ）
(function () {
    'use strict';

    const SLOT_SELECTOR = '.bi-adsense__slot:not([data-bi-ad-observed])';
    const isApp = !!(window.Capacitor && typeof window.Capacitor.isNativePlatform === 'function' && window.Capacitor.isNativePlatform());
    const canObserve = 'IntersectionObserver' in window;
    let observer = null;

    function getObserver() {
        if (observer) return observer;
        observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                const el = entry.target;
                if (!entry.isIntersecting || !el.offsetWidth) return;
                observer.unobserve(el);
                el.classList.add('adsbygoogle');
                try {
                    (window.adsbygoogle = window.adsbygoogle || []).push({});
                } catch (e) { /* 広告が読み込めなくても画面は使える */ }
            });
        }, { rootMargin: '200px 0px' });
        return observer;
    }

    function scan(root) {
        const slots = (root || document).querySelectorAll(SLOT_SELECTOR);
        if (!slots.length) return;
        slots.forEach((el) => {
            if (isApp || !canObserve) {
                const wrapper = el.closest('.bi-adsense');
                if (wrapper) wrapper.remove();
                return;
            }
            el.setAttribute('data-bi-ad-observed', '');
            getObserver().observe(el);
        });
    }

    window.biScanAdUnits = scan;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => scan());
    } else {
        scan();
    }
})();
