// Form Auto-Fill — Playwright browser bridge
// Usage: node bridge.js <action>
// Input JSON from stdin, result JSON to stdout, progress logs to stderr.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const LOGIN_HOST = 'login.example.com';

function readInput() {
  const raw = fs.readFileSync(0, 'utf8');
  return raw.trim() ? JSON.parse(raw) : {};
}

function out(obj) {
  process.stdout.write(JSON.stringify(obj, null, 1) + '\n');
}

async function gotoRetry(page, url, attempts = 4) {
  let lastErr = null;
  for (let i = 0; i < attempts; i++) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      return;
    } catch (e) {
      lastErr = e;
      console.error(`RETRY ${i + 1}: ${e.message || e}`);
      await page.waitForTimeout(2500 * (i + 1));
    }
  }
  throw lastErr || new Error('Failed to load page');
}

async function ensureLogin(page, ctx, authPath, headed) {
  const waitFrame = async () => {
    for (let i = 0; i < 20; i++) {
      const f = page.frames().find(x => x.url().includes(LOGIN_HOST));
      if (f) return f;
      await page.waitForTimeout(1000);
    }
    return null;
  };

  let lf = await waitFrame();
  if (!lf) return true;
  if (!headed) return false;

  console.error('NEED_LOGIN: Complete CAPTCHA in browser window');
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    const still = page.frames().some(f => f.url().includes(LOGIN_HOST));
    if (!still) {
      try { await ctx.storageState({ path: authPath }); } catch (e) {}
      return true;
    }
    await page.waitForTimeout(2000);
  }
  return false;
}

// ── DOM Interaction Helpers ────────────────────────────────────────

async function findItemIndex(page, keyword) {
  const items = page.locator('.form-item');
  const n = await items.count();
  for (let i = 0; i < n; i++) {
    const t = await items.nth(i).evaluate(n =>
      (n.textContent || '').replace(/\s+/g, ' ')
    ).catch(() => '');
    if (t.includes(keyword)) return i;
  }
  return -1;
}

async function pickOption(page, keyword, text) {
  const idx = await findItemIndex(page, keyword);
  if (idx < 0) throw new Error('Item not found: ' + keyword);
  const item = page.locator('.form-item').nth(idx);
  const li = await page.evaluate(({ qidx, prefix }) => {
    const el = document.querySelectorAll('.form-item')[qidx];
    if (!el) return -1;
    const labels = Array.from(el.querySelectorAll('label'));
    for (let i = 0; i < labels.length; i++) {
      if ((labels[i].textContent || '').trim().startsWith(prefix)) return i;
    }
    return -1;
  }, { qidx: idx, prefix: text.trim() });
  if (li < 0) throw new Error(`Option not found: [${keyword}] → ${text}`);
  const label = item.locator('label').nth(li);
  const cls = (await label.getAttribute('class')) || '';
  const checked = await label.locator('input').isChecked().catch(() => false);
  if (!checked && !cls.includes('checked')) {
    await label.click();
    await page.waitForTimeout(800);
  }
}

async function setSlider(page, keyword, target) {
  const idx = await findItemIndex(page, keyword);
  if (idx < 0) throw new Error('Slider not found: ' + keyword);
  const handle = page.locator('.form-item').nth(idx).locator('.slider-handle');
  await handle.focus();
  await page.waitForTimeout(300);
  for (let i = 0; i < 12; i++) {
    const v = await handle.getAttribute('aria-valuenow');
    const cur = v === null ? 1 : parseInt(v, 10);
    if (cur === target) break;
    await page.keyboard.press(cur < target ? 'ArrowRight' : 'ArrowLeft');
    await page.waitForTimeout(200);
  }
}

async function setCheck(page, keyword, text, wantChecked) {
  const idx = await findItemIndex(page, keyword);
  if (idx < 0) throw new Error('Item not found: ' + keyword);
  const item = page.locator('.form-item').nth(idx);
  const li = await page.evaluate(({ qidx, prefix }) => {
    const el = document.querySelectorAll('.form-item')[qidx];
    if (!el) return -1;
    const labels = Array.from(el.querySelectorAll('label'));
    for (let i = 0; i < labels.length; i++) {
      if ((labels[i].textContent || '').trim().startsWith(prefix)) return i;
    }
    return -1;
  }, { qidx: idx, prefix: text.trim() });
  if (li < 0) throw new Error(`Checkbox not found: [${keyword}] → ${text}`);
  const label = item.locator('label').nth(li);
  const cls = (await label.getAttribute('class')) || '';
  const checked = await label.locator('input').isChecked().catch(() => false);
  if ((checked || cls.includes('checked')) !== wantChecked) {
    await label.click();
    await page.waitForTimeout(700);
  }
}

async function resetMulti(page, keyword) {
  const idx = await findItemIndex(page, keyword);
  if (idx < 0) return;
  const labels = page.locator('.form-item').nth(idx).locator('label');
  const n = await labels.count();
  for (let j = 0; j < n; j++) {
    const label = labels.nth(j);
    const cls = (await label.getAttribute('class')) || '';
    const checked = await label.locator('input').isChecked().catch(() => false);
    if (checked || cls.includes('checked')) {
      await label.click();
      await page.waitForTimeout(250);
    }
  }
}

async function setRichText(page, text) {
  const frame = page.frames().find(f => f.url() === 'about:srcdoc' && f !== page.mainFrame());
  if (!frame) throw new Error('Rich text editor not found');
  const body = frame.locator('[contenteditable="true"], #editor').first();
  await body.click();
  const mod = process.platform === 'darwin' ? 'Meta' : 'Control';
  await page.keyboard.press(`${mod}+A`);
  await page.keyboard.press('Backspace');
  await page.waitForTimeout(300);
  await page.keyboard.type(text, { delay: 15 });
  await page.waitForTimeout(500);
}

async function getSliderValue(page, keyword) {
  const idx = await findItemIndex(page, keyword);
  if (idx < 0) return null;
  return page.locator('.form-item').nth(idx).locator('.slider-handle')
    .getAttribute('aria-valuenow');
}

async function verifyPick(page, keyword, text) {
  const idx = await findItemIndex(page, keyword);
  if (idx < 0) return false;
  const li = await page.evaluate(({ qidx, prefix }) => {
    const el = document.querySelectorAll('.form-item')[qidx];
    if (!el) return -1;
    const labels = Array.from(el.querySelectorAll('label'));
    for (let i = 0; i < labels.length; i++) {
      if ((labels[i].textContent || '').trim().startsWith(prefix)) return i;
    }
    return -1;
  }, { qidx: idx, prefix: text.trim() });
  if (li < 0) return false;
  const label = page.locator('.form-item').nth(idx).locator('label').nth(li);
  const cls = (await label.getAttribute('class')) || '';
  const checked = await label.locator('input').isChecked().catch(() => false);
  return cls.includes('checked') || checked;
}

// ── Fill Logic ─────────────────────────────────────────────────────

async function fillVertical(page, ans) {
  const checks = [];
  const pick = async (kw, val) => {
    if (!val || !String(val).trim()) throw new Error(`[${kw}] is empty`);
    await pickOption(page, kw, String(val));
    checks.push([kw, val, await verifyPick(page, kw, String(val))]);
  };

  await pickOption(page, '条目是', `${ans.uid}:${ans.name}`);
  checks.push(['Q1', ans.uid, await verifyPick(page, '条目是', `${ans.uid}:${ans.name}`)]);

  await pick('字段A', ans.f1 || '3');
  await pick('字段B', ans.f2 || '3');
  await pick('字段C', ans.f3 || '3');
  await setSlider(page, '整体满意度', parseInt(ans.total || '5', 10));

  for (const [k, kw] of [['g1', '子项-1'], ['g2', '子项-2'], ['g3', '子项-3']]) {
    const score = parseInt(String(ans[k] || '3').split(':')[0], 10);
    await setSlider(page, kw, score);
  }

  await pick('是否推荐', ans.rec || '不一定');
  const recNote = (ans.recNote || '').trim();
  if (recNote) await setRichText(page, recNote);

  await pick('标记A', ans.flag1 ? '是' : '否');
  await pick('标记B', ans.flag2 ? '是' : '否');

  await resetMulti(page, '标签');
  for (const k of ['sub1', 'sub2']) {
    const v = (ans[k] || '').trim();
    if (v) await setCheck(page, '标签', v, true);
  }

  for (const [k, kw] of [['w1', '意愿1'], ['w2', '意愿2']]) {
    await pickOption(page, kw, ans[k] !== false ? '是' : '否');
    checks.push([kw, ans[k] !== false ? '是' : '否', await verifyPick(page, kw, ans[k] !== false ? '是' : '否')]);
  }
  await pick('意愿3', ans.w3 || '不一定');

  return { checks };
}

async function submitForm(page) {
  const btn = page.locator('button', { hasText: '提交' }).last();
  await btn.scrollIntoViewIfNeeded().catch(() => {});
  await page.waitForTimeout(400);
  let text = '';
  for (let attempt = 0; attempt < 3; attempt++) {
    text = await page.evaluate(() => document.body?.innerText || '');
    if (text.includes('提交成功')) break;
    await btn.click({ timeout: 8000 }).catch(() => {});
    for (let i = 0; i < 15; i++) {
      await page.waitForTimeout(1000);
      text = await page.evaluate(() => document.body?.innerText || '');
      if (text.includes('提交成功')) return { submitted: true, pageText: text.slice(0, 400) };
    }
  }
  return { submitted: text.includes('提交成功'), pageText: text.slice(0, 400) };
}

// ── Main ───────────────────────────────────────────────────────────

async function main() {
  const action = process.argv[2];
  const input = readInput();
  const authPath = input.authPath || path.join(__dirname, 'auth.json');
  const url = input.url;
  const dryRun = !!input.dryRun;

  if (!url) { out({ ok: false, error: 'Missing url' }); process.exit(1); }

  let browser = null;
  try {
    const launch = async (headed) => {
      browser = await chromium.launch({
        channel: 'chrome', headless: !headed,
        args: ['--disable-blink-features=AutomationControlled'],
      });
      const ctx = await browser.newContext({
        viewport: { width: 1200, height: 900 }, locale: 'zh-CN',
        ...(fs.existsSync(authPath) ? { storageState: authPath } : {}),
      });
      await ctx.addInitScript(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      });
      return { ctx, page: await ctx.newPage() };
    };

    let { ctx, page } = await launch(false);
    await gotoRetry(page, url);
    await page.waitForTimeout(5000);
    let logged = await ensureLogin(page, ctx, authPath, false);
    if (!logged) {
      await browser.close();
      ({ ctx, page } = await launch(true));
      await gotoRetry(page, url);
      await page.waitForTimeout(5000);
      logged = await ensureLogin(page, ctx, authPath, true);
      if (!logged) throw new Error('Login timeout');
    }

    if (action === 'check_login') {
      out({ ok: true, loggedIn: true });
      await browser.close();
      return;
    }

    if (action === 'open') {
      const items = await page.evaluate(() => {
        const els = document.querySelectorAll('.form-item select option, .form-item input[type="radio"]');
        const seen = new Set();
        const result = [];
        els.forEach(el => {
          const val = el.value || el.textContent;
          if (val && !seen.has(val)) { seen.add(val); result.push(val); }
        });
        return result;
      });
      out({ ok: true, url: page.url(), items });
      await browser.close();
      return;
    }

    if (action === 'fill') {
      const ans = input.answers || {};
      const res = await fillVertical(page, ans);
      const allOk = res.checks.every(c => c[2] === true);
      const result = { ok: true, allOk, checks: res.checks.map(c => ({ label: c[0], want: c[1], ok: c[2] })) };
      if (dryRun || !allOk) {
        result.submitted = false;
        if (!allOk) result.error = 'Validation failed';
        out(result);
        await browser.close();
        return;
      }
      const sub = await submitForm(page);
      result.submitted = sub.submitted;
      result.pageText = sub.pageText;
      out(result);
      await browser.close();
      return;
    }

    out({ ok: false, error: 'Unknown action: ' + action });
  } catch (e) {
    out({ ok: false, error: e.stack || String(e) });
    if (browser) await browser.close().catch(() => {});
    process.exit(0);
  }
}

main();
