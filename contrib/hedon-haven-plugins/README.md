# Porntrex.tv plugin (Hedon Haven)

Two things live in this folder:

| File | Purpose |
|---|---|
| `porntrex.dart` | A **new** plugin written against the PornTrex (KVS) site structure, implementing the same `PluginInterface` as the Pornhub plugin. |
| `rename_domain.sh` | The literal find/replace you asked for: swaps `pornhub.com` → `porntrex.tv` inside your own copy of `pornhub.dart` (optionally also class name / codeName / prettyName with `--full`). |

## Why a rewrite and not just a domain swap

Replacing the domain string alone leaves a plugin that talks to PornTrex with
Pornhub's logic. Everything below is Pornhub-specific and would break instantly:

* the `ss` cookie + `data-token` from `#searchInput`, the `KEY` compute-check
  JavaScript, `/api/v1/video/search_autocomplete`, `/comment/show`;
* DOM selectors (`#singleFeedSection`, `li[data-video-vkey]`,
  `ul#videoListSearchResults`, `.userInfoContainer`, `#relatedVideos`,
  `#cmtContent`);
* the media extraction (`#mobileContainer > script` → `mediaDefinitions` HLS)
  and the sprite timeline (`thumbs.spritePatterns` / `samplingFrequency`).

## What PornTrex actually looks like (verified 2026-09-04)

* `porntrex.tv` and `porntrex.com` serve the same KVS installation — the `.tv`
  host works fine, so the plugin uses it.
* Watch page: `https://www.porntrex.tv/video/<numeric id>/<slug>`
* Search: `https://www.porntrex.tv/search/<query>/[<sorting>/][<length>/]`
  * sorting segments: `latest-updates`, `most-popular`, `top-rated`,
    `longest`, `most-commented`, `most-favourited`
  * length segments: `ten-min`, `ten-thirty-min`, `thirty-all-min`
* Listing pagination via the KVS async API (returns a bare HTML fragment):
  `?mode=async&function=get_block&block_id=<block>&sort_by=<...>&from=<page>`
  * homepage block: `list_videos_latest_videos_list`
  * search block: `list_videos_videos_list_search_result` (+ `q`, `from_videos`)
  * author block: `list_videos_common_videos_list`
* Authors come in three flavours: `/members/<numeric id>/` (uploaders),
  `/models/<slug>/`, `/channels/<slug>/`. The plugin stores author ids as
  `type/slug` so it never has to guess twice.
* Media urls come from `var flashvars = { ... }` in the watch page:
  `video_url`, `video_alt_url`, `video_alt_url2`, … each with a `*_text`
  label (`480p`, `1080p`, `4K`). Progressive MP4, not HLS.
  When a url starts with `function/0/`, the 32-character hash inside its path
  is shuffled and must be restored using `license_code` — implemented in
  `_deobfuscateKvsUrl` / `_licenseToken`.
* No search-autocomplete endpoint exists (`/search_autocomplete/` → 404 page),
  so `getSearchSuggestions` returns an empty list.
* No sprite timeline: only ~10 static screenshots per video
  (`.../300x168/<n>.jpg`), so `isolateGetProgressThumbnails` reports
  "unsupported" instead of faking a seek preview.

## Known caveats

The sandbox that produced this file can read PornTrex only through a
text-extraction proxy, so **class names in the DOM selectors could not be
byte-verified**. Every selector is written defensively (multiple candidates +
fallbacks), but expect to tighten these while running the plugin against the
live site:

* thumb grid items (`div.video-item` / `div.item` + fallback on
  `a[href*="/video/"]`),
* comment block (`.comment-item` / `.comment`) — PornTrex may require a login
  for comments,
* author page statistics rows and avatar/banner.

Everything that is protocol-level (URLs, async block ids, flashvars, the
license-code de-obfuscation) is verified or taken from the documented KVS
behaviour and should be correct.

## Using the rename script instead

```bash
chmod +x rename_domain.sh
./rename_domain.sh ../../path/to/pornhub.dart          # domain only
./rename_domain.sh ../../path/to/pornhub.dart --full   # + identifiers
```

It leaves a `.bak` next to the original and prints any remaining `pornhub`
occurrences (cookie key `accessAgeDisclaimerPH` and the `testingMap` video ids
are deliberately untouched — they are Pornhub-specific data, not URLs).
