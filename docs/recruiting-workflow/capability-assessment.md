# Boss Capability Assessment

Date: 2026-07-08

This assessment maps the requested Boss Zhipin portion of the recruiting workflow to the current `boss-cli` repository.

## Requested Boss-Side Flow

The requested Boss-side phase is:

1. Detect that a candidate has messaged the recruiter on Boss Zhipin.
2. Read the private message content and candidate resume/profile.
3. Evaluate the candidate against the active JD using rules such as keywords, work experience, and role fit.
4. Reply with a standardized screening message after the wording is confirmed.
5. Guide the candidate to add a specified WeChat account.

## Current Support Matrix

| Requirement | Current support | Evidence / command | Notes |
| --- | --- | --- | --- |
| Read recruiter inbox | Supported | `boss recruiter inbox --json` | Reads candidate list and last messages through recruiter friend-list APIs. |
| Read chat history | Supported | `boss recruiter chat <friendId> --json` | Uses `get_boss_chat_history` in `boss_cli/client.py`. |
| Read candidate profile/resume | Supported | `boss recruiter resume <encryptGeekId> --json` | Uses `get_boss_view_geek`; may require `encryptGeekId`, job ID, and valid security context. |
| Read posted jobs / active JDs | Supported | `boss recruiter jobs --json` | Lists recruiter-side posted jobs available to the current account. |
| Search or browse candidate pool | Supported | `boss recruiter search`, `boss recruiter recommend` | Useful for proactive sourcing, not strictly required for inbound-message workflow. |
| Send standardized reply | Supported at API level | `boss recruiter reply <friendId> "message" -y` | This can send text replies to an existing candidate conversation. |
| Ask for WeChat exchange | Partially supported | `boss recruiter exchange-wechat <friendId> -y` | Relies on Boss exchange API and may be blocked by anti-bot or session requirements. |
| Detect new messages automatically | Not built in | External polling required | The repo has CLI commands but no long-running service, scheduler, or event listener. |
| Candidate-to-JD rule evaluation | Not built in | Needs new code | Data can be fetched; matching logic must be implemented outside or added to repo. |
| Workflow state tracking | Not built in | Needs SQLite/Excel/DB layer | Required for dedupe, stage tracking, timestamps, and auditability. |
| Boss-side tagging/archival | Mostly not built in | `boss recruiter labels` only reads labels | No clear command for setting candidate tags or notes. Use local state until write APIs are discovered. |

## Practical Conclusion

The repository has enough Boss-side primitives to build the first automation stage:

```text
poll recruiter inbox
  -> identify unprocessed or newly updated candidates
  -> fetch chat history and candidate resume/profile
  -> evaluate candidate against configured JD rules
  -> send approved standardized reply with WeChat guidance
  -> persist candidate state and timestamps locally
```

It is not yet a complete workflow product. The missing pieces are orchestration, rules, templates, persistence, observability, and a clean abstraction boundary for later channels.

## Important Caveats

- The Boss API is reverse-engineered. Endpoint behavior can change without notice.
- Some actions need fresh browser-derived cookies and `__zp_stoken__`.
- Automated messaging should use conservative rate limits and clear compliance rules.
- Candidate data is sensitive. Avoid printing full personal data in logs or docs.
- Human approval should be required before enabling any new outbound message template in production.
