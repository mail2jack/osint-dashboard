import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// ── Configuration ───────────────────────────────────────────────────────────
const BASE_URL = __ENV.BASE_URL || "http://localhost:5000";

const LOGIN_USER = __ENV.LOGIN_USER || "admin";
const LOGIN_PASS = __ENV.LOGIN_PASS || "Test1234!";

const VIRTUAL_USERS = __ENV.VUS ? parseInt(__ENV.VUS) : 5;
const DURATION = __ENV.DURATION || "30s";

// ── Custom metrics ──────────────────────────────────────────────────────────
const vesselDuration = new Trend("vessel_lookup_duration_ms");
const healthDuration = new Trend("health_check_duration_ms");

export const options = {
  vus: VIRTUAL_USERS,
  duration: DURATION,
  thresholds: {
    http_req_duration: ["p(95)<500"],  // 95% of requests under 500ms
    http_req_failed: ["rate<0.01"],    // <1% failure rate
  },
};

// ── Session setup — login once ──────────────────────────────────────────────
export function setup() {
  const loginUrl = `${BASE_URL}/cms/login`;
  const payload = {
    username: LOGIN_USER,
    password: LOGIN_PASS,
  };
  const res = http.post(loginUrl, JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
  });

  const cookies = res.cookies;
  return { cookies };
}

// ── VU code — runs for each VU iteration ────────────────────────────────────
export default function (data) {
  const jar = http.cookieJar();

  // 1. Health check (quick)
  {
    const res = http.get(`${BASE_URL}/health?quick=1`);
    healthDuration.add(res.timings.duration);
    check(res, {
      "health status 200": (r) => r.status === 200,
    });
  }
  sleep(1);

  // 2. API version
  {
    const res = http.get(`${BASE_URL}/api/version`);
    check(res, {
      "version status 200": (r) => r.status === 200,
    });
  }
  sleep(1);

  // 3. Vessel lookup (async — parallel sources)
  {
    const res = http.post(
      `${BASE_URL}/cms/api/vessel-lookup`,
      JSON.stringify({ imo: "9811000" }),
      { headers: { "Content-Type": "application/json" } }
    );
    vesselDuration.add(res.timings.duration);
    check(res, {
      "vessel status 200": (r) => r.status === 200,
      "vessel found": (r) => r.json("found") === true,
    });
  }
  sleep(2);

  // 4. Dashboard (authenticated)
  {
    const res = http.get(`${BASE_URL}/cms/dashboard`);
    check(res, {
      "dashboard status 200": (r) => r.status === 200,
    });
  }
  sleep(1);
}

// ── Teardown ─────────────────────────────────────────────────────────────────
export function teardown(data) {
  // Logout
  http.post(`${BASE_URL}/cms/logout`);
}
