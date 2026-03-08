# Real Viewing Request Tool — Implementation Research

This document summarizes research findings on implementing a **true viewing request tool** to replace the current `simulate_viewing_request` tool. The goal is to automate the submission of viewing requests via listing URLs (e.g., Realtor.ca): navigate to the URL, click "Request a showing", fill the form with personal details and requested time, and submit.

---

## 1. Feasibility Assessment

### 1.1 What Can Be Automated

The following steps are straightforward to automate with browser automation (Playwright or Selenium):

| Step | Feasibility |
|------|-------------|
| **Navigate** to listing URL | High — Direct navigation |
| **Click** "Request a showing" | High — Requires selector discovery per site |
| **Fill form** (name, email, phone, time) | High — Standard form interaction |
| **Submit** the form | High — After CAPTCHA is resolved |

### 1.2 The CAPTCHA Challenge

Realtor.ca and similar listing sites typically use security measures such as:

- **Incapsula** (Imperva) — bot protection / traffic filtering
- **reCAPTCHA v2** — "I'm not a robot" checkbox
- Possibly **hCaptcha** or **Cloudflare Turnstile** — depending on the page

These are designed to block automated access. Automation frameworks can click the checkbox element, but the underlying challenge analyzes:

- Mouse movements and behavioral signals
- Browser fingerprinting
- Whether the browser is headless/automated

### 1.3 Approach Options

| Approach | Feasibility | Notes |
|----------|-------------|-------|
| **Semi-automated (user solves CAPTCHA)** | High | Open headed browser, fill form automatically, pause at CAPTCHA, user solves it manually, then script submits. Reliable and avoids ToS concerns. |
| **Third-party CAPTCHA solvers** | Medium | Services like 2Captcha, CapSolver, Anti-Captcha. Adds cost, latency (~5–15 s), and may violate site ToS. |
| **Fully automated bypass** | Low | Playwright stealth / tricks. Unreliable; detection is sophisticated; high maintenance. |

### 1.4 Recommended Pattern: Semi-Automated

Use **headed** Playwright (visible browser):

1. Navigate to listing URL
2. Click "Request a showing"
3. Fill the form with user details and requested time
4. **Pause** and prompt the user: "Please solve the CAPTCHA in the browser window"
5. Either poll for CAPTCHA completion (e.g., button becomes enabled) and auto-submit, or let the user click Submit after solving

This keeps the tool practical without relying on third-party CAPTCHA solvers.

### 1.5 Technical Considerations

- **Realtor.ca structure** — Selectors for "Request a showing", form fields, and CAPTCHA must be discovered and may change when the site is updated.
- **Session/auth** — Handle cookies or multi-step flows if the site requires them.
- **Rate limiting** — Avoid submitting too many requests in a short window.
- **Environment** — On a headless server, headed mode needs a display (e.g., Xvfb) or a desktop session; local development is simpler.

---

## 2. Third-Party CAPTCHA Solving Services

For fully automated flows (without user interaction), third-party services solve CAPTCHAs via API. Most charge **per solve** (often quoted per 1,000 solves).

### 2.1 Service Comparison

| Service | reCAPTCHA v2 (per 1,000) | reCAPTCHA v3 (per 1,000) | Notes |
|---------|--------------------------|--------------------------|-------|
| **2Captcha** | €0.99 – €2.80 (~$1–$3) | €1.40 – €2.80 (~$1.50–$3) | Price varies with demand; refunds for failures |
| **CapSolver** | ~$0.50 – $0.80 | ~$1.00 | Often cheaper; volume discounts to ~$0.10/1K |
| **Anti-Captcha** | $0.95 – $2.00 | $1.00 – $2.00 | Volume discounts; ~5 s solve time |
| **CapMonster Cloud** | ~$1–$3 | ~$1–$3 | Fast for Turnstile |
| **DeathByCaptcha** | ~$1–$3 | ~$1–$3 | Similar to others |

*Prices are approximate; check each provider’s site for current rates.*

### 2.2 2Captcha (2captcha.com)

| CAPTCHA Type | Price per 1,000 solves |
|--------------|------------------------|
| Normal / image | $0.50 – $1.00 |
| reCAPTCHA v2 | €0.99 – €2.80 (~$1–$3) |
| reCAPTCHA v3 | €1.40 – €2.80 (~$1.50–$3) |
| Cloudflare Turnstile | €1.40 (~$1.50) |
| Arkose Labs (FunCaptcha) | €1.40 – €50 |
| GeeTest | €2.80 |

- Minimum around **$0.005 per solve** when demand is low
- Prices rise under high load
- Refunds for incorrect solves

### 2.3 CapSolver (capsolver.com)

| CAPTCHA Type | Price per 1,000 solves |
|--------------|------------------------|
| reCAPTCHA v2 | ~$0.50 – $0.80 |
| reCAPTCHA v2 Enterprise | ~$1.00 |
| reCAPTCHA v3 | ~$1.00 |
| reCAPTCHA v3 Enterprise | ~$3.00 |

- Often cheaper than 2Captcha
- Package discounts can reduce to ~$0.10/1,000
- Solve times: ~3–9 seconds
- High reported success rate (>95%)

### 2.4 Anti-Captcha (anti-captcha.com)

| CAPTCHA Type | Price per 1,000 solves |
|--------------|------------------------|
| Image CAPTCHA | $0.50 – $0.70 |
| reCAPTCHA v2 | $0.95 – $2.00 |
| reCAPTCHA v3 | $1.00 – $2.00 |
| reCAPTCHA Enterprise | ~$5.00 |
| hCaptcha | ~$2.00 |
| Turnstile | ~$2.00 |

- Volume discounts by daily usage
- Solve time ~5 seconds

### 2.5 Cost for Viewing Request Use Case

Assuming Realtor.ca uses **reCAPTCHA v2** (or similar):

- **Per solve:** ~$0.001 – $0.003
- **Light use (5–20 requests/week):** ~$0.05 – $0.20/week
- **Heavy use (100 requests/week):** ~$0.50 – $1/week

### 2.6 Additional Considerations for CAPTCHA Services

1. **Minimum balance** — Most services require a prepaid balance (e.g., $3–5).
2. **Latency** — Each solve adds ~5–15 seconds to the request.
3. **Terms of service** — Automated CAPTCHA solving may violate Realtor.ca’s ToS.
4. **Reliability** — Success rates are high but not 100%; occasional failures or timeouts.

---

## 3. References

- [rental-search-assistant-mvp-technical-spec.md](rental-search-assistant-mvp-technical-spec.md) — Current tool contracts, including `simulate_viewing_request`
- [todo](../todo) — Item: "Implement a true viewing_request tool"
- [rental-search-assistant-use-case.md](rental-search-assistant-use-case.md) — Future `submit_viewing_request` tool discussion

---

*Document created from implementation research (February 2026).*
