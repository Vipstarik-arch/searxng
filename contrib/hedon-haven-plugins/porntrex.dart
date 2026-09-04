import 'dart:isolate';

import 'package:flutter/services.dart';
import 'package:html/dom.dart';
import 'package:html/parser.dart';
import 'package:html_unescape/html_unescape.dart';
import 'package:http/http.dart';

import '/services/external_link_manager.dart';
import '/utils/bundled_plugin.dart';
import '/utils/exceptions.dart';
import '/utils/global_vars.dart';
import '/utils/plugin_interface/plugin_interface.dart';
import '/utils/try_parse.dart';
import '/utils/universal_formats.dart';

/// Plugin for porntrex.tv (KVS - Kernel Video Sharing engine).
///
/// Notes on the site, gathered while writing this plugin:
///   * porntrex.tv and porntrex.com serve the very same KVS installation.
///   * Listings are paginated either via a path segment (/latest-updates/3/)
///     or via the KVS async block API:
///       ?mode=async&function=get_block&block_id=<block>&from=<page>
///     The async API returns a raw HTML fragment (no layout) and is what this
///     plugin uses wherever possible, because it is much cheaper to download.
///   * Video sources live in a `var flashvars = {...}` object inside the watch
///     page. Direct mp4 links may be obfuscated ("function/0/..."), in which
///     case a 32 character hash inside the path has to be de-shuffled with the
///     `license_code` value. See [_deobfuscateKvsUrl].
///   * There is no search-autocomplete endpoint on this site.
class PorntrexPlugin extends BundledPlugin implements PluginInterface {
  @override
  final bool isBundledPlugin = true;
  @override
  String codeName = "com.hedon_haven.porntrex";
  @override
  String prettyName = "Porntrex.tv";
  @override
  String developer = "Hedon Haven";
  @override
  String contactEmail = "contact@hedon-haven.top";
  @override
  String issueTrackerUrl = "https://issues.hedon-haven.top";
  @override
  String description = "Account-less functionality for porntrex.tv";
  @override
  Uri iconUrl = Uri.parse("https://www.porntrex.tv/favicon.ico");
  @override
  String serviceUrl = "https://www.porntrex.tv";
  @override
  List<String> handleUrls = [
    // Homepage
    "https://www.porntrex.tv/",
    "https://www.porntrex.tv/latest-updates/",
    "https://www.porntrex.tv/top-rated/",
    "https://www.porntrex.tv/most-popular/",
    // Search page
    "https://www.porntrex.tv/search/",
    // Video page
    "https://www.porntrex.tv/video/",
    // Author pages
    "https://www.porntrex.tv/members/",
    "https://www.porntrex.tv/models/",
    "https://www.porntrex.tv/channels/",
  ];
  @override
  int initialHomePage = 1;
  @override
  int initialSearchResultsPage = 1;
  @override
  int initialCommentsPage = 1;
  @override
  int initialVideoSuggestionsPage = 1;
  @override
  int initialAuthorVideosPage = 1;

  // Inherited from PluginInterface, unused for bundled plugins
  @override
  Uri? updateUrl;
  @override
  String version = "";

  @override
  Map<String, dynamic> testingMap = {
    "ignoreScrapedErrors": {
      "homepage": ["thumbnailBinary", "maxQuality", "lastWatched", "addedOn"],
      "searchResults": [
        "thumbnailBinary",
        "maxQuality",
        "lastWatched",
        "addedOn"
      ],
      "videoMetadata": [
        "chapters",
        "actors",
        "authorAvatar",
        "authorSubscriberCount",
        "ratingsNegativeTotal"
      ],
      "videoSuggestions": [
        "thumbnailBinary",
        "maxQuality",
        "lastWatched",
        "addedOn"
      ],
      "authorVideos": [
        "thumbnailBinary",
        "maxQuality",
        "authorName",
        "authorID",
        "lastWatched",
        "addedOn"
      ],
      "comments": [
        "authorID",
        "countryID",
        "orientation",
        "ratingsPositiveTotal",
        "ratingsNegativeTotal",
        "profilePicture"
      ],
      "authorPage": [
        "aliases",
        "banner",
        "rank",
        "advancedDescription",
        "externalLinks",
        "lastViewed",
        "addedOn"
      ]
    },
    "testingVideos": [
      {"videoID": "2734863", "progressThumbnailsAmount": 0},
      {"videoID": "2748328", "progressThumbnailsAmount": 0}
    ],
    "testingAuthorPageIds": [
      // A member (uploader) type author
      "members/6917393",
      // A model type author
      "models/melissa-moore",
    ]
  };

  static const String _base = "https://www.porntrex.tv";

  /// KVS block ids. They are stable per installation and were read off the
  /// rendered pages.
  static const String _blockLatest = "list_videos_latest_videos_list";
  static const String _blockCommon = "list_videos_common_videos_list";
  static const String _blockSearch = "list_videos_videos_list_search_result";

  /// Search sorting is encoded as a path segment on KVS, not a query parameter
  final Map<String, String> _sortingTypeMap = {
    "Relevance": "",
    "Upload date": "latest-updates",
    "Views": "most-popular",
    "Rating": "top-rated",
    "Duration": "longest",
  };

  /// Only these three duration buckets exist on the site
  final Map<int, String> _durationMap = {
    0: "",
    600: "ten-min",
    1800: "ten-thirty-min",
    3600: "thirty-all-min",
  };

  bool _pluginIsInitialized = false;

  Map<String, String> get _defaultHeaders => {
        "User-Agent": httpUserAgent,
        "Referer": "$_base/",
      };

  // ---------------------------------------------------------------------------
  // Networking
  // ---------------------------------------------------------------------------

  Future<Response> _get(Uri uri, {Map<String, String>? headers}) async {
    logger.d("Requesting $uri");
    final response = await client.get(uri, headers: {
      ..._defaultHeaders,
      // KVS shows a "you must be 18" interstitial without this
      "Cookie": "kt_tcookie=1; kt_is_visited=1; kt_ips=1",
      ...?headers,
    });
    if (response.statusCode == 404) {
      throw NotFoundException();
    }
    if (response.statusCode != 200) {
      logger.e("Error downloading $uri: ${response.statusCode} "
          "- ${response.reasonPhrase}");
      throw Exception("Error downloading $uri: ${response.statusCode} "
          "- ${response.reasonPhrase}");
    }
    return response;
  }

  /// Build a KVS async-block request, which returns a bare html fragment
  Uri _asyncBlock(String path, String blockId,
      {int page = 1,
      String sortBy = "",
      Map<String, String> extra = const {}}) {
    return Uri.parse("$_base$path").replace(queryParameters: {
      "mode": "async",
      "function": "get_block",
      "block_id": blockId,
      "sort_by": sortBy,
      "from": "$page",
      ...extra,
    });
  }

  // ---------------------------------------------------------------------------
  // Small parsing helpers
  // ---------------------------------------------------------------------------

  /// "17 967 views" / "1.2K" / "just added" -> int
  int? _parseCount(String? raw) {
    if (raw == null) return null;
    String value = raw
        .toLowerCase()
        .replaceAll("views", "")
        .replaceAll("view", "")
        .replaceAll("\u00a0", "")
        .replaceAll(",", "")
        .replaceAll(" ", "")
        .trim();
    if (value.isEmpty) return null;
    double multiplier = 1;
    if (value.endsWith("k")) {
      multiplier = 1000;
    } else if (value.endsWith("m")) {
      multiplier = 1000000;
    } else if (value.endsWith("b")) {
      multiplier = 1000000000;
    }
    if (multiplier != 1) value = value.substring(0, value.length - 1);
    final parsed = double.tryParse(value);
    if (parsed == null) return null;
    return (parsed * multiplier).round();
  }

  /// "30:30" or "1:02:15" -> Duration
  Duration? _parseDuration(String? raw) {
    if (raw == null) return null;
    final List<String> parts = raw.trim().split(":").reversed.toList();
    if (parts.isEmpty || parts.length > 3) return null;
    const List<int> factors = [1, 60, 3600];
    int seconds = 0;
    for (int i = 0; i < parts.length; i++) {
      final int? value = int.tryParse(parts[i].trim());
      if (value == null) return null;
      seconds += value * factors[i];
    }
    return Duration(seconds: seconds);
  }

  /// "3 hours ago" / "1 year ago" / "yesterday" -> DateTime.
  /// Unlike a naive implementation this also handles values above 9.
  DateTime? _parseRelativeDate(String? raw) {
    if (raw == null) return null;
    final text = raw.toLowerCase().trim();
    if (text == "yesterday") {
      return DateTime.now().subtract(const Duration(days: 1));
    }
    if (text == "today" || text == "just now") return DateTime.now();
    final match = RegExp(r"(\d+)\s*(second|minute|hour|day|week|month|year)")
        .firstMatch(text);
    if (match == null) {
      logger.w("Could not convert date string to DateTime: $raw");
      return null;
    }
    final amount = int.parse(match.group(1)!);
    switch (match.group(2)!) {
      case "second":
        return DateTime.now().subtract(Duration(seconds: amount));
      case "minute":
        return DateTime.now().subtract(Duration(minutes: amount));
      case "hour":
        return DateTime.now().subtract(Duration(hours: amount));
      case "day":
        return DateTime.now().subtract(Duration(days: amount));
      case "week":
        return DateTime.now().subtract(Duration(days: amount * 7));
      case "month":
        return DateTime.now().subtract(Duration(days: amount * 30));
      default:
        return DateTime.now().subtract(Duration(days: amount * 365));
    }
  }

  /// "1080pHD" / "4K" / "2160p" -> 1080 / 2160 / 2160
  int? _parseQualityLabel(String? raw) {
    if (raw == null) return null;
    final text = raw.toLowerCase().trim();
    if (text.contains("4k")) return 2160;
    final match = RegExp(r"(\d{3,4})\s*p").firstMatch(text);
    return match == null ? null : int.tryParse(match.group(1)!);
  }

  /// The numeric id inside /video/<id>/<slug>/
  String? _videoIdFromHref(String? href) {
    if (href == null) return null;
    final match = RegExp(r"/video/(\d+)").firstMatch(href);
    return match?.group(1);
  }

  /// KVS serves protocol relative ("//cdn/...") and root relative ("/foo")
  /// urls interchangeably -> always hand a fully qualified url to the app
  String? _absolute(String? href) {
    if (href == null || href.trim().isEmpty) return null;
    final String value = href.trim();
    if (value.startsWith("http")) return value;
    if (value.startsWith("//")) return "https:$value";
    return "$_base${value.startsWith("/") ? "" : "/"}$value";
  }

  /// Turns "/models/foo/" or "https://.../members/123/" into "models/foo" /
  /// "members/123", the author id format used throughout this plugin
  String? _authorIdFromHref(String? href) {
    if (href == null) return null;
    final List<String> segments =
        href.split("/").where((e) => e.isNotEmpty).toList();
    if (segments.length < 2) return null;
    final String type = segments[segments.length - 2];
    if (!["members", "models", "channels"].contains(type)) return null;
    return "$type/${segments.last}";
  }

  // ---------------------------------------------------------------------------
  // Video list parsing
  // ---------------------------------------------------------------------------

  /// Parses a KVS thumb grid. Works both on full pages and on async fragments.
  List<UniversalVideoPreview> _parseVideoList(Document html,
      [bool authorPageMode = false]) {
    // KVS wraps every thumb in .video-item / .item; ads are plain <a> or iframes
    List<Element> items = html.querySelectorAll(
        'div.video-item, div.item, div[class*="video-item"]');
    if (items.isEmpty) {
      // Fallback: any anchor that points at a watch page and holds an image
      items = html
          .querySelectorAll('a[href*="/video/"]')
          .where((e) => e.querySelector("img") != null)
          .toList();
    }
    logger.d("Parsing ${items.length} video elements (some might be ads!)");

    final List<UniversalVideoPreview> results = [];
    final Set<String> seenIds = {};

    for (final Element item in items) {
      final Element? link = item.localName == "a"
          ? item
          : item.querySelector('a[href*="/video/"]');
      final String? iD = _videoIdFromHref(link?.attributes["href"]);
      // Skip duplicates (KVS repeats the link for image and title)
      if (iD != null && !seenIds.add(iD)) continue;

      final Element? img = item.querySelector("img");
      final String? thumbnail = _absolute(img?.attributes["data-original"] ??
          img?.attributes["data-src"] ??
          img?.attributes["src"]);

      String? title = item.querySelector('.title, strong.title')?.text.trim();
      title ??= link?.attributes["title"]?.trim();
      title ??= img?.attributes["alt"]?.trim();
      if (title != null && title.isEmpty) title = null;

      final Duration? duration =
          _parseDuration(item.querySelector('.duration')?.text);
      final int? views = _parseCount(item.querySelector('.views')?.text);
      final int? ratingPercent = tryParse(() => int.parse(item
          .querySelector('.rating, .rating-item, .likes')!
          .text
          .replaceAll("%", "")
          .trim()));
      final int? maxQuality =
          _parseQualityLabel(item.querySelector('.is-hd, .quality')?.text);

      // Inline webm/mp4 preview shown on hover
      final String? preview = _absolute(img?.attributes["data-preview"] ??
          link?.attributes["data-preview"] ??
          item.attributes["data-preview"]);

      final Element? authorDiv =
          item.querySelector('a[href*="/members/"], a[href*="/models/"], '
              'a[href*="/channels/"]');

      final UniversalVideoPreview uniResult = UniversalVideoPreview(
        // Don't enforce null safety here, report via scrapeFailMessage below
        iD: iD ?? "null",
        title: title ?? "null",
        plugin: this,
        thumbnail: thumbnail,
        thumbnailHttpHeaders: _defaultHeaders,
        previewVideo: preview == null ? null : tryParse(() => Uri.parse(preview)),
        previewVideoHttpHeaders: _defaultHeaders,
        duration: duration,
        viewsTotal: views,
        ratingsPositivePercent: ratingPercent,
        maxQuality: maxQuality,
        virtualReality: false,
        authorName: authorDiv?.text.trim(),
        authorID: _authorIdFromHref(authorDiv?.attributes["href"]),
        // Porntrex has no verification badge on thumbs
        verifiedAuthor: false,
      );

      uniResult.verifyScrapedData(
          codeName,
          authorPageMode
              ? testingMap["ignoreScrapedErrors"]["authorVideos"]
              : testingMap["ignoreScrapedErrors"]["homepage"]);

      if (iD == null || title == null) {
        uniResult.scrapeFailMessage =
            "Error: Failed to scrape critical variable(s):"
            "${iD == null ? " ID" : ""}"
            "${title == null ? " title" : ""}";
      }

      results.add(uniResult);
    }
    return results;
  }

  // ---------------------------------------------------------------------------
  // KVS flashvars / obfuscated url handling
  // ---------------------------------------------------------------------------

  /// Extracts `var flashvars = { ... }` into a flat string map
  Map<String, String> _parseFlashvars(Document html) {
    String? raw;
    for (final Element script in html.querySelectorAll("script")) {
      if (script.text.contains("flashvars")) {
        raw = script.text;
        break;
      }
    }
    if (raw == null) {
      throw Exception("Could not find flashvars in video page");
    }
    final int start = raw.indexOf("{", raw.indexOf("flashvars"));
    if (start == -1) {
      throw Exception("Found a flashvars script, but no object literal in it");
    }
    // Walk the braces to find the end of the object
    int depth = 0;
    int end = -1;
    for (int i = start; i < raw.length; i++) {
      if (raw[i] == "{") depth++;
      if (raw[i] == "}") {
        depth--;
        if (depth == 0) {
          end = i;
          break;
        }
      }
    }
    if (end == -1) {
      throw Exception("Unterminated flashvars object in video page");
    }
    final String body = raw.substring(start, end + 1);

    final Map<String, String> flashvars = {};
    final RegExp entry = RegExp(
        r"""([a-zA-Z0-9_]+)\s*:\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|([\w./-]+))""");
    for (final RegExpMatch match in entry.allMatches(body)) {
      final String key = match.group(1)!;
      final String value =
          match.group(2) ?? match.group(3) ?? match.group(4) ?? "";
      flashvars.putIfAbsent(key, () => value.replaceAll(r"\/", "/"));
    }
    return flashvars;
  }

  /// KVS turns the license code into a shuffling key
  List<int> _licenseToken(String licenseCode) {
    // Keep digits only - the code is usually written as "$1234567890"
    final String clean = licenseCode.replaceAll(RegExp(r"[^0-9]"), "");
    if (clean.length < 2) {
      logger.w("license_code has no usable digits: $licenseCode");
      return [];
    }
    final List<int> values = clean.split("").map(int.parse).toList();

    String modified = clean.replaceAll("0", "1");
    final int center = modified.length ~/ 2;
    // A very long license code would overflow a 64 bit int -> use BigInt
    final BigInt front = BigInt.parse(modified.substring(0, center + 1));
    final BigInt back = BigInt.parse(modified.substring(center));
    modified = (BigInt.from(4) * (front - back).abs()).toString();
    if (modified.length > center + 1) {
      modified = modified.substring(0, center + 1);
    }

    final List<int> token = [];
    for (int index = 0; index < modified.length; index++) {
      final int current = int.parse(modified[index]);
      for (int offset = 0; offset < 4; offset++) {
        if (index + offset >= values.length) break;
        token.add((values[index + offset] + current) % 10);
      }
    }
    return token;
  }

  /// Turns "function/0/https://cdn/get_file/1/<shuffled hash>/..." into a
  /// playable url by restoring the original order of the 32 char hash
  String _deobfuscateKvsUrl(String videoUrl, String? licenseCode) {
    const String prefix = "function/0/";
    if (!videoUrl.startsWith(prefix)) return videoUrl;
    if (licenseCode == null || licenseCode.isEmpty) {
      logger.w("Obfuscated video url but no license_code found");
      return videoUrl.substring(prefix.length);
    }

    final Uri parsed = Uri.parse(videoUrl.substring(prefix.length));
    final List<int> token = _licenseToken(licenseCode);
    final List<String> segments = parsed.path.split("/");
    const int hashLength = 32;
    // segments[0] is empty (leading slash), the hash always sits at index 3
    if (segments.length <= 3 ||
        segments[3].length < hashLength ||
        token.length < hashLength) {
      logger.w("Obfuscated url does not have the expected KVS layout: "
          "${parsed.path} (token length ${token.length})");
      return parsed.toString();
    }

    final String hash = segments[3].substring(0, hashLength);
    final List<int> indices = List<int>.generate(hashLength, (i) => i);

    int accumulator = 0;
    for (int source = hashLength - 1; source >= 0; source--) {
      accumulator += token[source];
      final int destination = (source + accumulator) % hashLength;
      final int swap = indices[source];
      indices[source] = indices[destination];
      indices[destination] = swap;
    }

    segments[3] = indices.map((i) => hash[i]).join() +
        segments[3].substring(hashLength);
    return parsed.replace(path: segments.join("/")).toString();
  }

  // ---------------------------------------------------------------------------
  // PluginInterface implementation
  // ---------------------------------------------------------------------------

  @override
  Future<void> init(String cachePath,
      [void Function(String body)? debugCallback]) async {
    if (_pluginIsInitialized) return;
    logger.i("Initializing $codeName plugin");
    final Response response = await _get(Uri.parse("$_base/"));
    debugCallback
        ?.call("Headers: ${response.headers}\n\nBody: ${response.body}");
    final Document html = parse(response.body);
    // KVS age gate: a full page interstitial with a "disclaimer" wrapper
    if (html.querySelector("#disclaimer, .age-verification") != null &&
        html.querySelector('a[href*="/video/"]') == null) {
      throw AgeGateException();
    }
    _pluginIsInitialized = true;
  }

  @override
  Future<bool> runFunctionalityTest() {
    // Bundled plugins are checked by the daily CI instead
    return Future.value(true);
  }

  @override
  Future<ExternalLinkParsed> parseExternalLink(Uri uri) async {
    logger.i("Parsing ${uri.path}");
    final List<String> segments = uri.pathSegments;

    if (segments.isEmpty) {
      return ExternalLinkParsed(type: ContentType.homePage, pageCount: 1);
    }

    switch (segments.first) {
      case "video":
        // /video/<id>/<slug>/
        if (segments.length < 2) {
          return ExternalLinkParsed(type: ContentType.unknown);
        }
        return ExternalLinkParsed(
            type: ContentType.videoPage, iD: segments[1]);

      case "search":
        // /search/<query>/<sorting>/<length>/<page>/
        if (segments.length < 2) {
          return ExternalLinkParsed(type: ContentType.unknown);
        }
        final String query =
            Uri.decodeComponent(segments[1]).replaceAll("-", " ");
        String sorting = "Relevance";
        int minDuration = 0;
        int maxDuration = 3600;
        int page = 1;
        for (final String segment in segments.skip(2)) {
          final int? asNumber = int.tryParse(segment);
          if (asNumber != null) {
            page = asNumber;
          } else if (_sortingTypeMap.containsValue(segment)) {
            sorting = _sortingTypeMap.entries
                .firstWhere((entry) => entry.value == segment)
                .key;
          } else if (_durationMap.containsValue(segment)) {
            final int bucket = _durationMap.entries
                .firstWhere((entry) => entry.value == segment)
                .key;
            if (bucket == 600) {
              maxDuration = 600;
            } else if (bucket == 1800) {
              minDuration = 600;
              maxDuration = 1800;
            } else {
              minDuration = 1800;
            }
          }
        }
        return ExternalLinkParsed(
          type: ContentType.searchResultsPage,
          searchRequest: UniversalSearchRequest(
            searchString: query,
            sortingType: sorting,
            dateRange: "All time",
            minDuration: minDuration,
            maxDuration: maxDuration,
          ),
          pageCount: page,
        );

      case "members" || "models" || "channels":
        if (segments.length < 2) {
          return ExternalLinkParsed(type: ContentType.unknown);
        }
        return ExternalLinkParsed(
          type: ContentType.authorPage,
          iD: "${segments.first}/${segments[1]}",
        );

      case "latest-updates" || "top-rated" || "most-popular":
        return ExternalLinkParsed(
          type: ContentType.homePage,
          pageCount: int.tryParse(segments.length > 1 ? segments[1] : "") ?? 1,
        );

      default:
        return ExternalLinkParsed(type: ContentType.unknown);
    }
  }

  @override
  Future<List<UniversalVideoPreview>> getHomePage(int page,
      [void Function(String body)? debugCallback]) async {
    final Response response = await _get(_asyncBlock(
        "/latest-updates/", _blockLatest,
        page: page, sortBy: "post_date"));
    debugCallback?.call(response.body);
    return _parseVideoList(parse(response.body));
  }

  // downloadThumbnail is implemented at the BundledPlugin level

  @override
  Future<List<String>> getSearchSuggestions(String searchString,
      [void Function(String body)? debugCallback]) async {
    // Porntrex has no autocomplete endpoint (verified: /search_autocomplete/
    // returns the 404 dinosaur page)
    debugCallback?.call("Porntrex does not provide search suggestions");
    return [];
  }

  @override
  Future<List<UniversalVideoPreview>> getSearchResults(
      UniversalSearchRequest request, int page,
      [void Function(String body)? debugCallback]) async {
    if (request.searchString.isEmpty) return [];

    // Sorting and duration filters are path segments, e.g.
    //   /search/milf/top-rated/thirty-all-min/
    final String query =
        Uri.encodeComponent(request.searchString.trim().replaceAll(RegExp(r"\s+"), "-"));
    final String sorting = _sortingTypeMap[request.sortingType] ?? "";
    // Only three buckets exist: 0-10, 10-30 and 30+ minutes. Pick the one that
    // overlaps the requested range best instead of over-filtering.
    String durationSegment = "";
    if (request.maxDuration > 0 && request.maxDuration <= 600) {
      durationSegment = _durationMap[600]!;
    } else if (request.minDuration >= 1800) {
      durationSegment = _durationMap[3600]!;
    } else if (request.minDuration >= 600 &&
        request.maxDuration > 0 &&
        request.maxDuration <= 1800) {
      durationSegment = _durationMap[1800]!;
    }

    final String path = "/search/$query/"
        "${sorting.isEmpty ? "" : "$sorting/"}"
        "${durationSegment.isEmpty ? "" : "$durationSegment/"}";

    final Response response = await _get(_asyncBlock(path, _blockSearch,
        page: page,
        // The sorting already lives in the path; sending it again as
        // sort_by makes KVS ignore the path variant
        sortBy: "",
        extra: {
          "q": request.searchString,
          // KVS uses a dedicated pagination parameter on search pages
          "from_videos": "$page",
          if (request.minQuality >= 720) "hd": "1",
        }));
    debugCallback?.call(response.body);

    final Document html = parse(response.body);
    final String pageText = html.body?.text.toLowerCase() ?? "";
    if (pageText.contains("no videos found") ||
        pageText.contains("nothing found")) {
      return [];
    }
    return _parseVideoList(html);
  }

  @override
  Future<Uri?> getVideoUriFromID(String videoID) async {
    return Uri.parse("$_base/video/$videoID/");
  }

  @override
  Future<UniversalVideoMetadata> getVideoMetadata(
      String videoId, UniversalVideoPreview uvp,
      [void Function(String body)? debugCallback]) async {
    final Response response = await _get(Uri.parse("$_base/video/$videoId/"));
    debugCallback?.call(response.body);
    final Document rawHtml = parse(response.body);
    final Map<String, String> flashvars = _parseFlashvars(rawHtml);
    final String? licenseCode = flashvars["license_code"];

    // Collect video_url, video_alt_url, video_alt_url2 ... plus their labels.
    // Unlabelled sources fall back to a quality parsed out of the file name,
    // and never overwrite an already known, better identified source.
    final Map<int, Uri> qualityUris = {};
    for (final MapEntry<String, String> entry in flashvars.entries) {
      if (!RegExp(r"^video_(alt_)?url\d*$").hasMatch(entry.key)) continue;
      if (entry.value.isEmpty) continue;
      final String real = _deobfuscateKvsUrl(entry.value, licenseCode);
      int? quality = _parseQualityLabel(flashvars["${entry.key}_text"]);
      // e.g. ".../foo_1080p.mp4" or ".../1080p.mp4"
      quality ??= _parseQualityLabel(
          RegExp(r"(\d{3,4})p").firstMatch(real)?.group(0));
      // Last resort: keep the source, but below every identified quality
      quality ??= qualityUris.isEmpty ? 0 : qualityUris.keys.reduce((a, b) => a < b ? a : b) - 1;
      qualityUris.putIfAbsent(quality, () => Uri.parse(real));
    }
    if (qualityUris.isEmpty) {
      throw Exception("Could not extract any video url from flashvars");
    }

    String? textOf(String selector) {
      final String? text = rawHtml.querySelector(selector)?.text.trim();
      return (text == null || text.isEmpty) ? null : HtmlUnescape().convert(text);
    }

    // The info block is a list of <div class="item"> rows labelled in bold
    String? valueOfInfoRow(String label) {
      for (final Element row
          in rawHtml.querySelectorAll('.info .item, .video-info .item')) {
        if (row.text.toLowerCase().contains(label.toLowerCase())) {
          return row.text.split(":").last.trim();
        }
      }
      return null;
    }

    List<String> linkTexts(String hrefFragment) => rawHtml
        .querySelectorAll('a[href*="$hrefFragment"]')
        .map((e) => e.text.trim())
        .where((e) => e.isNotEmpty)
        .toSet()
        .toList();

    final Element? uploader =
        rawHtml.querySelector('a[href*="/members/"], a[href*="/channels/"]');

    final List<String> categories = linkTexts("/categories/");
    final List<String> tags = linkTexts("/tags/");

    List<({String name, String authorID, String avatar})>? actors;
    final Set<String> seenActors = {};
    for (final Element model
        in rawHtml.querySelectorAll('a[href*="/models/"]')) {
      final String name = model.text.trim();
      final String? id = _authorIdFromHref(model.attributes["href"]);
      if (name.isEmpty || id == null || !seenActors.add(id)) continue;
      actors ??= [];
      actors.add((
        name: name,
        authorID: id,
        avatar: _absolute(model.querySelector("img")?.attributes["src"]) ?? ""
      ));
    }

    final int? ratingPercent = tryParse(() => int.parse(rawHtml
        .querySelector('.rating-container .voters, .rating .percent, .rating')!
        .text
        .replaceAll("%", "")
        .trim()));
    final int? viewsTotal = _parseCount(valueOfInfoRow("views") ??
        rawHtml.querySelector('.views .icon-eye, .views')?.text);

    final UniversalVideoMetadata metadata = UniversalVideoMetadata(
        iD: videoId,
        // The app expects a quality -> uri map here. Porntrex serves plain mp4
        // progressive files instead of hls playlists, which every player that
        // can handle m3u8 can also handle.
        m3u8Uris: qualityUris,
        playbackHttpHeaders: _defaultHeaders,
        title: textOf("h1") ?? flashvars["video_title"] ?? "null",
        plugin: this,
        universalVideoPreview: uvp,
        authorID: _authorIdFromHref(uploader?.attributes["href"]) ?? "null",
        authorName: uploader?.text.trim(),
        authorSubscriberCount:
            _parseCount(rawHtml.querySelector('.subscribe .count')?.text),
        authorAvatar: _absolute(rawHtml
            .querySelector('.avatar img, .member-avatar img')
            ?.attributes["src"]),
        actors: actors,
        description: textOf('.info .item:last-child, .video-description'),
        viewsTotal: viewsTotal,
        tags: tags,
        categories: categories,
        uploadDate: _parseRelativeDate(valueOfInfoRow("added") ??
            rawHtml.querySelector('.added em, .added')?.text),
        // Porntrex only publishes a percentage, never like/dislike totals.
        // The percentage already reached the app through the preview object
        // (see _parseVideoList), which is the only place that has a field for it.
        ratingsPositiveTotal: null,
        ratingsNegativeTotal: null,
        ratingsTotal: null,
        virtualReality: false,
        chapters: null,
        rawHtml: rawHtml);

    if (ratingPercent == null) {
      logger.d("No rating percentage found on the watch page");
    }

    metadata.verifyScrapedData(
        codeName, testingMap["ignoreScrapedErrors"]["videoMetadata"]);
    return metadata;
  }

  // getProgressThumbnails is implemented at the BundledPlugin level

  @override
  Future<void> isolateGetProgressThumbnails(SendPort sendPort) async {
    final ReceivePort receivePort = ReceivePort();
    sendPort.send(receivePort.sendPort);
    final List message = await receivePort.first as List;
    final RootIsolateToken rootToken = message[0] as RootIsolateToken;
    final SendPort resultsPort = message[1] as SendPort;
    final SendPort logPort = message[2] as SendPort;
    final SendPort fetchPort = message[3] as SendPort;
    final Document rawHtml = message[5] as Document;

    BackgroundIsolateBinaryMessenger.ensureInitialized(rootToken);
    try {
      // Porntrex does not ship a sprite timeline. It only exposes ~10 static
      // screenshots (.../300x168/<n>.jpg), which is far too coarse for a
      // seek preview -> report "unsupported" instead of faking a timeline.
      final List<Element> screenshots =
          rawHtml.querySelectorAll('img[src*="/300x168/"]');
      logPort.send([
        "info",
        "Porntrex has no progress thumbnail sprites "
            "(${screenshots.length} static screenshots only)"
      ]);
      resultsPort.send(null);
    } catch (e, stackTrace) {
      logPort.send(
          ["error", "Error in isolateGetProgressThumbnails: $e\n$stackTrace"]);
      resultsPort.send(null);
    }
  }

  // cancelGetProgressThumbnails is implemented at the BundledPlugin level

  @override
  Future<Uri?> getCommentUriFromID(String commentID, String videoID) {
    return Future.value(Uri.parse("$_base/video/$videoID/#comment$commentID"));
  }

  @override
  Future<List<UniversalComment>> getComments(
      String videoID, Document rawHtml, int page,
      [void Function(String body)? debugCallback]) async {
    // KVS returns the whole comment list in one block and simply repeats it
    // when asked for a second page -> stop after the first one, otherwise the
    // app would keep appending duplicates forever
    if (page > initialCommentsPage) {
      debugCallback?.call("Porntrex returns all comments in one go");
      return [];
    }

    // Comments are loaded through their own async block
    final Response response;
    try {
      response = await _get(_asyncBlock(
          "/video/$videoID/", "video_comments_video_comments",
          page: page, extra: {"from_comments": "$page"}));
    } catch (e) {
      // Commenting requires an account on Porntrex; the block can be missing
      logger.w("Could not load comments for $videoID: $e");
      return [];
    }
    debugCallback?.call(response.body);

    final Document html = parse(response.body);
    final List<UniversalComment> comments = [];

    for (final Element element
        in html.querySelectorAll('.comment-item, .comment')) {
      final String? author =
          element.querySelector('.author, .username, .name')?.text.trim();
      final String? body =
          element.querySelector('.text, .comment-text, .message')?.text.trim();
      String? iD = element.attributes["data-comment-id"];
      if (iD == null && element.id.isNotEmpty) {
        iD = element.id.replaceAll(RegExp(r"[^0-9]"), "");
        if (iD.isEmpty) iD = null;
      }

      final UniversalComment comment = UniversalComment(
        iD: iD ?? "null",
        videoID: videoID,
        author: author ?? "null",
        commentBody: body ?? "null",
        hidden: false,
        plugin: this,
        authorID: _authorIdFromHref(
            element.querySelector('a[href*="/members/"]')?.attributes["href"]),
        countryID: null,
        orientation: null,
        profilePicture:
            _absolute(element.querySelector("img")?.attributes["src"]),
        ratingsPositiveTotal: null,
        ratingsNegativeTotal: null,
        ratingsTotal: _parseCount(element.querySelector('.rating')?.text),
        commentDate:
            _parseRelativeDate(element.querySelector('.added, .date')?.text),
        replyComments: [],
      );

      comment.verifyScrapedData(
          codeName, testingMap["ignoreScrapedErrors"]["comments"]);

      if (iD == null || author == null || body == null) {
        comment.scrapeFailMessage =
            "Error: Failed to scrape critical variable(s):"
            "${iD == null ? " iD" : ""}"
            "${author == null ? " author" : ""}"
            "${body == null ? " commentBody" : ""}";
      }
      comments.add(comment);
    }
    return comments;
  }

  @override
  Future<List<UniversalVideoPreview>> getVideoSuggestions(
      String videoID, Document rawHtml, int page,
      [void Function(String body)? debugCallback]) async {
    if (page > 1) {
      // Related videos are rendered once, together with the watch page
      debugCallback?.call("Porntrex doesn't allow loading more suggestions");
      return [];
    }
    debugCallback?.call(rawHtml.outerHtml);
    final Element? related = rawHtml.querySelector(
        '#list_videos_related_videos_items, .related-videos, #related_videos');
    if (related == null) {
      logger.w("No related videos container found");
      return [];
    }
    return _parseVideoList(parse(related.innerHtml));
  }

  @override
  Future<Uri?> getAuthorUriFromID(String authorID) async {
    logger.i("Getting author page URL of: $authorID");
    // Author ids created by this plugin already carry their type prefix
    if (authorID.contains("/")) return Uri.parse("$_base/$authorID/");

    // Numeric ids are uploader (member) profiles
    if (int.tryParse(authorID) != null) {
      return Uri.parse("$_base/members/$authorID/");
    }
    for (final String type in ["models", "channels"]) {
      final Uri candidate = Uri.parse("$_base/$type/$authorID/");
      logger.d("Checking http status of: $candidate");
      final Response head = await client.head(candidate, headers: {
        ..._defaultHeaders,
        "Cookie": "kt_tcookie=1; kt_is_visited=1; kt_ips=1",
      });
      if (head.statusCode == 200) return candidate;
    }
    throw Exception("Could not resolve author page for $authorID "
        "(tried members, models, channels)");
  }

  @override
  Future<UniversalAuthorPage> getAuthorPage(String authorID,
      [void Function(String body)? debugCallback]) async {
    final Uri authorUri = (await getAuthorUriFromID(authorID))!;
    final Response response = await _get(authorUri);
    debugCallback?.call(response.body);
    final Document html = parse(response.body);

    final String? name = html
            .querySelector('.headline h1, h1.title, .username')
            ?.text
            .trim() ??
        html.querySelector("title")?.text.split("|").first.trim();

    String? description = html
        .querySelector('.about .text, .description, .info .item')
        ?.text
        .trim();
    if (description != null) {
      description = HtmlUnescape().convert(description);
      if (description.isEmpty) description = null;
    }

    // KVS renders the counters as "<em>1 234</em> videos" style rows
    int? statistic(String label) {
      for (final Element row in html.querySelectorAll(
          '.info .item, .stats li, .list-info li, .headline .item')) {
        final String text = row.text.toLowerCase();
        if (!text.contains(label)) continue;
        final Match? number = RegExp(r"[\d][\d  \u00a0,.]*").firstMatch(text);
        if (number == null) continue;
        final int? parsed = _parseCount(number.group(0));
        if (parsed != null) return parsed;
      }
      return null;
    }

    Map<String, Uri>? externalLinks;
    for (final Element link
        in html.querySelectorAll('.about a[href^="http"]')) {
      final String? href = link.attributes["href"];
      if (href == null) continue;
      final Uri? parsed = Uri.tryParse(href);
      if (parsed == null) continue;
      externalLinks ??= {};
      externalLinks[link.text.trim().isEmpty ? href : link.text.trim()] =
          parsed;
    }

    final UniversalAuthorPage authorPage = UniversalAuthorPage(
      iD: authorID,
      name: name,
      plugin: this,
      avatar: _absolute(html
          .querySelector('.avatar img, .member-avatar img, .thumb img')
          ?.attributes["src"]),
      banner: _absolute(
          html.querySelector('.cover img, .banner img')?.attributes["src"]),
      // Porntrex has no aliases
      aliases: null,
      description: description,
      advancedDescription: null,
      externalLinks: externalLinks,
      viewsTotal: statistic("view"),
      videosTotal: statistic("video"),
      subscribers: statistic("subscriber"),
      rank: null,
      rawHtml: html,
    );

    authorPage.verifyScrapedData(
        codeName, testingMap["ignoreScrapedErrors"]["authorPage"]);
    return authorPage;
  }

  @override
  Future<List<UniversalVideoPreview>> getAuthorVideos(String authorID, int page,
      [void Function(String body)? debugCallback]) async {
    final Uri authorUri = (await getAuthorUriFromID(authorID))!;
    final Response response;
    try {
      response = await _get(_asyncBlock(authorUri.path, _blockCommon,
          page: page, sortBy: "post_date"));
    } on NotFoundException {
      // 404 here means both "error" and "no videos" -> treat as end of list
      logger.w("404 while loading author videos -> treating as no more videos");
      return [];
    }
    debugCallback?.call(response.body);

    final Document html = parse(response.body);
    if (html.querySelector('.empty, .no-results') != null) return [];
    return _parseVideoList(html, true);
  }
}
