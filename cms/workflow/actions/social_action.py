import json
import logging


logger = logging.getLogger(__name__)


def _social_scan(action):
    findings = []
    from cms.social_extractor import detect_platform, MAJOR_SOCIAL_PLATFORMS
    from cms.username_search import search_username, search_username_maigret

    # Collect accounts with their subject context
    accounts_with_subject = []

    if action.data_value:
        try:
            raw = json.loads(action.data_value)
            if isinstance(raw, list):
                for item in raw:
                    subject_id = None
                    if isinstance(item, dict):
                        subject_id = item.get("subject_id")
                    accounts_with_subject.append((item, subject_id))
        except (json.JSONDecodeError, TypeError):
            pass

    if not accounts_with_subject:
        for subject in action.case.subjects or []:
            sa_query = getattr(subject, "social_accounts", None)
            has_social = False
            if sa_query is not None:
                try:
                    count = sa_query.count()
                except Exception:
                    logger.debug(
                        "Failed to count social accounts for subject %s",
                        subject.id,
                        exc_info=True,
                    )
                    count = 0
                if count > 0:
                    has_social = True
                    for sa in sa_query:
                        accounts_with_subject.append((sa, subject.id))
            if not has_social and subject.name:
                accounts_with_subject.append((subject.name, subject.id))

    if not accounts_with_subject:
        findings.append(
            {
                "title": "No social media accounts to scan",
                "detail": "No social media accounts have been provided for this subject.",
                "source_type": "social",
                "icon": "🌐",
                "verified": False,
            }
        )
        return findings

    for acct, subject_id in accounts_with_subject:
        username = None
        label = None
        url = None
        input_platform = None

        if isinstance(acct, str):
            username = acct.strip()
            if username.startswith("@"):
                username = username[1:]
            label = username
        else:
            url = (
                getattr(acct, "url", None)
                if not isinstance(acct, dict)
                else acct.get("url")
            )
            input_platform = (
                getattr(acct, "platform", None)
                if not isinstance(acct, dict)
                else acct.get("platform")
            )
            username = (
                getattr(acct, "username", None)
                if not isinstance(acct, dict)
                else acct.get("username")
            )
            if not username and url:
                username = url.rstrip("/").split("/")[-1]
            label = input_platform or username or "unknown"

        if not username:
            continue

        seen_sites = set()

        try:
            maigret_result = search_username_maigret(username)
            if maigret_result.get("found_count", 0) > 0:
                for f in maigret_result.get("findings", []):
                    site = f.get("site") or f.get("platform", "")
                    if f.get("exists") == True and site not in seen_sites:
                        seen_sites.add(site)
                        result_url = f.get("url", "")
                        platform = detect_platform(result_url)
                        finding = {
                            "title": f"{label}: profile active ({site})",
                            "detail": f"Found via Maigret. URL: {result_url}",
                            "source_url": result_url,
                            "source_type": "social",
                            "icon": "🌐",
                            "verified": False,
                            "subject_id": subject_id,
                            "screenshots": [{"url": None, "source_url": result_url}],
                        }
                        if platform and platform in MAJOR_SOCIAL_PLATFORMS:
                            finding["social_account"] = {
                                "platform": platform,
                                "username": username,
                                "url": result_url,
                            }
                        findings.append(finding)
        except Exception as e:
            logger.warning("Maigret search for %s failed: %s", username, e)

        try:
            sherlock_result = search_username(username)
            if sherlock_result.get("found_count", 0) > 0:
                for f in sherlock_result.get("findings", []):
                    site = f.get("platform") or f.get("site", "")
                    if f.get("exists") == True and site not in seen_sites:
                        seen_sites.add(site)
                        result_url = f.get("url", "")
                        platform = detect_platform(result_url)
                        finding = {
                            "title": f"{label}: profile active ({site})",
                            "detail": f"Found via Sherlock. URL: {result_url}",
                            "source_url": result_url,
                            "source_type": "social",
                            "icon": "🌐",
                            "verified": False,
                            "subject_id": subject_id,
                            "screenshots": [{"url": None, "source_url": result_url}],
                        }
                        if platform and platform in MAJOR_SOCIAL_PLATFORMS:
                            finding["social_account"] = {
                                "platform": platform,
                                "username": username,
                                "url": result_url,
                            }
                        findings.append(finding)
        except Exception as e:
            logger.warning("Sherlock search for %s failed: %s", username, e)

    if not findings:
        for acct, subject_id in accounts_with_subject:
            if isinstance(acct, str):
                findings.append(
                    {
                        "title": f"Social media: {acct}",
                        "detail": f"Username: {acct}",
                        "source_type": "social",
                        "icon": "🌐",
                        "verified": False,
                        "subject_id": subject_id,
                    }
                )
            else:
                pl = (
                    getattr(acct, "platform", None)
                    if not isinstance(acct, dict)
                    else acct.get("platform", "unknown")
                )
                u = (
                    getattr(acct, "url", None)
                    if not isinstance(acct, dict)
                    else acct.get("url")
                )
                findings.append(
                    {
                        "title": f"Social media: {pl}",
                        "detail": f"URL: {u}" if u else f"Platform: {pl}",
                        "source_url": u,
                        "source_type": "social",
                        "icon": "🌐",
                        "verified": False,
                        "subject_id": subject_id,
                    }
                )

    return findings
