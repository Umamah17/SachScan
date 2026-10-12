"""Reddit bot: NOT BUILT (stub).

New Reddit API access requires manual approval under Reddit's Responsible Builder Policy, which takes
weeks, so for now the Reddit card on the website only links to our community.

TODO after approval:
  - register a "script" or "web" app at https://www.reddit.com/prefs/apps and request API access
  - listen to mentions (u/SachScanBot) / a moderator-enabled subreddit stream
  - convert each post or comment to backend.pipeline.core.NormalizedInput(platform="reddit", ...)
    and call backend.channels.common.handle_check(...)  (add "reddit" to the platform check constraints
    in supabase/migrations first)
  - reply with backend.pipeline.formatter.format_chat(result, bold="**")
  - flag only; never remove content
"""


def main() -> None:
    raise SystemExit("The Reddit bot is not available yet: Reddit API access is pending approval. See bots/reddit_bot.py.")


if __name__ == "__main__":
    main()
