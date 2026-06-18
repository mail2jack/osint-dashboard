import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// ── Configuration ───────────────────────────────────────────────────────────
const BASE_URL = __ENV.BASE_URL || "http://localhost:5000";

export const options = {
  vus: 1,  // Single user — measuring latency, not throughput
  duration: "10s",
  thresholds: {
    vessel_lookup_duration_ms: ["p(95)<10000"],
  },
};

const vesselDuration = new Trend("vessel_lookup_duration_ms");

const TEST_QUERIES = [
  { imo: "9811000" },         // Ever Given (large container)
  { name: "EVER GIVEN" },
  { mmsi: "353136000" },
  { name: "MSC" },
  { eni: "02330000" },        // Inland vessel
];

export default function () {
  for (const query of TEST_QUERIES) {
    const res = http.post(
      `${BASE_URL}/cms/api/vessel-lookup`,
      JSON.stringify(query),
      { headers: { "Content-Type": "application/json" } }
    );

    vesselDuration.add(res.timings.duration);
    check(res, {
      "vessel status 200": (r) => r.status === 200,
      "vessel responded": (r) => r.json("found") !== undefined,
    });

    console.log(
      `Query ${JSON.stringify(query)} → ${res.status} (${res.timings.duration}ms)`
    );

    sleep(3);  // Respect rate limiting
  }
}
