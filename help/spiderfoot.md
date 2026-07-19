# SpiderFoot

SpiderFoot is an open-source OSINT automation tool integrated into the dashboard for performing reconnaissance scans. It collects information on IP addresses, domains, email addresses, usernames, and more from hundreds of open sources.

## Configuration

Before SpiderFoot can be used, the connection must be set up in **Settings**:

- **SpiderFoot URL** — e.g. `http://127.0.0.1:5001`
- **Username** — as configured in `~/.spiderfoot/passwd`
- **Password** — the corresponding password

SpiderFoot uses HTTP **Digest** authentication. The health status is checked every 60 seconds; on error, a red banner appears at the top of the page.

## Creating a Scan

1. Go to **SpiderFoot** → **New Scan**
2. Enter the **Target** (domain, IP address, email address, username, etc.)
3. Select a **Scan Type**:
   - **All** — use all modules (may take a long time)
   - **Footprint** — surface-level reconnaissance
   - **Investigate** — in-depth investigation
   - **Passive** — only passive sources (no direct connections)
   - **Custom** — choose modules yourself
4. Click **Start Scan**

## Scan Overview

The scan list shows:

- **ID** — scan identification number
- **Target** — the object being investigated
- **Status** — RUNNING / FINISHED / ERROR / FAILED / ABORTED
- **Results** — number of results found
- **Created** — creation date

Click on a scan to view the results.

## Scan Results

Results are grouped by **type** and **source module**:

| Type | Examples |
|------|----------|
| `SOCIAL_MEDIA` | Facebook, LinkedIn, Twitter profiles |
| `IP_ADDRESS` | IPv4/IPv6 addresses |
| `DOMAIN_NAME` | Associated domains |
| `EMAIL_ADDRESS` | Found email addresses |
| `PHONE_NUMBER` | Phone numbers |
| `GEO_INFO` | Location data |
| `NETBLOCK` | IP ranges |
| `WEB_CONTENT` | Web page content |
| `LEAKED_DATA` | Leaked credentials |

### SFURL Tags

Results may contain `<SFURL>` tags in the data. These are automatically detected and made clickable in the display.

## Linking Results

From the scan results you can:

- 🏷️ **Add to Case** — add a result as a **Finding** to an existing case
- 👤 **Create Subject** — create a new subject from discovered data

## Scan Types in Detail

### All
Iterates through all available modules. Most complete, but takes the longest.

### Footprint
Uses ~30 basic modules for a general reconnaissance of the target.

### Investigate
~50 modules focused on in-depth investigation of the found data.

### Passive
Only modules that make no direct connection to the target. Uses open sources such as DNS, WHOIS, search engines.

## Troubleshooting

**Scan stuck on RUNNING**: SpiderFoot service may have frozen. Restart the service: `sudo systemctl restart spiderfoot`

**Red banner "SpiderFoot is unreachable"**:
- Check if SpiderFoot is running: `curl http://127.0.0.1:5001`
- Check the URL in Settings
- Check the credentials in Settings
- Check if Digest auth is working

**No results**: Some scans take time. Large scans can take hours.
