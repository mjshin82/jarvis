// jarvis-web/src/static/i18n.js — 공용 UI 다국어(i18n) 시스템.
// <script src="/i18n.js"> 로 로드되어 window.I18N 을 노출한다.
// 브라우저에 텍스트로 서빙되므로 ES export 를 쓰지 않는다.
(function () {
  "use strict";

  var SUPPORTED = ["ko", "en", "ja"];
  var FALLBACK = "en";

  var STR = {
    ko: {
      "title": "회의 자막",
      "gate.title": "🔒 회의 자막 입장",
      "gate.pwPlaceholder": "비번",
      "gate.enter": "입장",
      "gate.errCannotEnter": "입장할 수 없습니다 (비번/회의 확인).",
      "gate.errEmptyPw": "비번을 입력하세요",
      "header.defaultTitle": "회의 자막",
      "lockbar.newCaptions": "↓ 새 자막 보기",
      "tab.log": "💬 대화기록",
      "tab.summarySuffix": " 요약",
      "lang.ko": "🇰🇷 한국어", "lang.en": "🇺🇸 English", "lang.ja": "🇯🇵 日本語", "lang.zh": "🇨🇳 中文",
      "conn.live": "● live",
      "conn.ended": "🛑 종료됨",
      "conn.disconnected": "⏸ 연결 끊김",
      "conn.locked": "🔒",
      "conn.reconnecting": "재연결 ({n}s)…",
      "card.meetingEnded": "— 회의 종료 —",
      "card.taken": "— 새 publisher 가 채널을 인수했습니다 —",
    },
    en: {
      "title": "Meeting Captions",
      "gate.title": "🔒 Enter Meeting Captions",
      "gate.pwPlaceholder": "Password",
      "gate.enter": "Enter",
      "gate.errCannotEnter": "Cannot enter (check password / meeting).",
      "gate.errEmptyPw": "Enter the password",
      "header.defaultTitle": "Meeting Captions",
      "lockbar.newCaptions": "↓ View new captions",
      "tab.log": "💬 Transcript",
      "tab.summarySuffix": " summary",
      "lang.ko": "🇰🇷 Korean", "lang.en": "🇺🇸 English", "lang.ja": "🇯🇵 Japanese", "lang.zh": "🇨🇳 Chinese",
      "conn.live": "● live",
      "conn.ended": "🛑 Ended",
      "conn.disconnected": "⏸ Disconnected",
      "conn.locked": "🔒",
      "conn.reconnecting": "Reconnecting ({n}s)…",
      "card.meetingEnded": "— Meeting ended —",
      "card.taken": "— A new publisher took over the channel —",
    },
    ja: {
      "title": "会議字幕",
      "gate.title": "🔒 会議字幕に入る",
      "gate.pwPlaceholder": "パスワード",
      "gate.enter": "入る",
      "gate.errCannotEnter": "入れません（パスワード／会議をご確認ください）。",
      "gate.errEmptyPw": "パスワードを入力してください",
      "header.defaultTitle": "会議字幕",
      "lockbar.newCaptions": "↓ 新しい字幕を見る",
      "tab.log": "💬 会話ログ",
      "tab.summarySuffix": " 要約",
      "lang.ko": "🇰🇷 韓国語", "lang.en": "🇺🇸 英語", "lang.ja": "🇯🇵 日本語", "lang.zh": "🇨🇳 中国語",
      "conn.live": "● live",
      "conn.ended": "🛑 終了",
      "conn.disconnected": "⏸ 切断",
      "conn.locked": "🔒",
      "conn.reconnecting": "再接続 ({n}s)…",
      "card.meetingEnded": "— 会議終了 —",
      "card.taken": "— 新しいパブリッシャーがチャンネルを引き継ぎました —",
    },
  };

  // 로케일 결정: ?lang= 우선 → navigator prefix → en.
  function resolveLocale(search, navLang) {
    try {
      var q = new URLSearchParams(search || "").get("lang");
      if (q && SUPPORTED.indexOf(q) !== -1) return q;
    } catch (e) { /* URLSearchParams 미지원 등 — navigator 로 진행 */ }
    var prefix = String(navLang || "").slice(0, 2).toLowerCase();
    if (SUPPORTED.indexOf(prefix) !== -1) return prefix;
    return FALLBACK;
  }

  // 카탈로그 조회(명시적 로케일) + {var} 치환. 절대 throw 하지 않는다.
  function translate(loc, key, vars) {
    var table = STR[loc] || STR[FALLBACK];
    var s = table[key];
    if (s == null) s = STR[FALLBACK][key];
    if (s == null) s = key;
    if (vars) {
      s = s.replace(/\{(\w+)\}/g, function (m, k) {
        return Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m;
      });
    }
    return s;
  }

  var locale = resolveLocale(
    (typeof location !== "undefined" && location.search) || "",
    (typeof navigator !== "undefined" && navigator.language) || ""
  );

  function t(key, vars) { return translate(locale, key, vars); }

  function apply(root) {
    root = root || document;
    var nodes = root.querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].textContent = t(nodes[i].getAttribute("data-i18n"));
    }
    var phs = root.querySelectorAll("[data-i18n-ph]");
    for (var j = 0; j < phs.length; j++) {
      phs[j].setAttribute("placeholder", t(phs[j].getAttribute("data-i18n-ph")));
    }
  }

  window.I18N = {
    locale: locale,
    t: t,
    apply: apply,
    _resolve: resolveLocale, // 테스트용 순수 함수
    _t: translate,           // 테스트용 순수 함수(명시적 로케일)
  };

  function init() {
    document.documentElement.lang = locale;
    apply(document);
  }
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }
})();
