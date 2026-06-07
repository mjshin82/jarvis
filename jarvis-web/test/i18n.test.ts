import { describe, it, expect, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dir = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(resolve(__dir, "../src/static/i18n.js"), "utf8");

// 서빙되는 IIFE 를 happy-dom 전역 window 에 대해 eval → window.I18N 노출.
// url 을 바꿔 ?lang= 동작을 검증한다.
function load(url = "http://localhost/") {
  (window as any).happyDOM.setURL(url);
  (0, eval)(SRC); // 간접 eval: 전역 스코프(window 등 전역)에서 실행
  return (window as any).I18N;
}

describe("I18N._resolve (로케일 우선순위)", () => {
  let I18N: any;
  beforeEach(() => { I18N = load(); });

  it("?lang=ja 가 navigator 보다 우선", () => {
    expect(I18N._resolve("?lang=ja", "ko-KR")).toBe("ja");
  });
  it("지원하지 않는 ?lang 은 무시하고 navigator 사용", () => {
    expect(I18N._resolve("?lang=fr", "ko-KR")).toBe("ko");
  });
  it("?lang 없으면 navigator prefix 사용", () => {
    expect(I18N._resolve("", "en-US")).toBe("en");
    expect(I18N._resolve("", "ja-JP")).toBe("ja");
  });
  it("지원하지 않는 navigator 는 en 으로 폴백", () => {
    expect(I18N._resolve("", "de-DE")).toBe("en");
  });
  it("navigator 없음/빈값은 en 으로 폴백", () => {
    expect(I18N._resolve("", "")).toBe("en");
    expect(I18N._resolve("", undefined)).toBe("en");
  });
});

describe("I18N._t (카탈로그 조회 + 치환)", () => {
  let I18N: any;
  beforeEach(() => { I18N = load(); });

  it("로케일별 값을 반환", () => {
    expect(I18N._t("ko", "tab.log")).toBe("💬 대화기록");
    expect(I18N._t("en", "tab.log")).toBe("💬 Transcript");
    expect(I18N._t("ja", "tab.log")).toBe("💬 会話ログ");
  });
  it("{n} 변수를 치환", () => {
    expect(I18N._t("ja", "conn.reconnecting", { n: 2 })).toBe("再接続 (2s)…");
    expect(I18N._t("en", "conn.reconnecting", { n: 4 })).toBe("Reconnecting (4s)…");
  });
  it("알 수 없는 로케일은 en 으로, 없는 키는 키 자체로 폴백", () => {
    expect(I18N._t("zz", "tab.log")).toBe("💬 Transcript");
    expect(I18N._t("en", "nonexistent.key")).toBe("nonexistent.key");
  });
  it("언어명은 국기 고정 + 이름만 로케일화", () => {
    expect(I18N._t("en", "lang.ja")).toBe("🇯🇵 Japanese");
    expect(I18N._t("ja", "lang.ko")).toBe("🇰🇷 韓国語");
    expect(I18N._t("ko", "lang.en")).toBe("🇺🇸 English");
  });
  it("list 네임스페이스 + nav 키를 로케일별로 반환", () => {
    expect(I18N._t("ko", "list.header")).toBe("최근 회의");
    expect(I18N._t("en", "list.header")).toBe("Recent meetings");
    expect(I18N._t("ja", "list.header")).toBe("最近の会議");
    expect(I18N._t("ko", "list.empty")).toBe("저장된 회의 없음");
    expect(I18N._t("en", "list.deleteConfirm")).toBe("Delete this meeting?");
    expect(I18N._t("ja", "list.liveDefault")).toBe("進行中の会議");
    expect(I18N._t("en", "list.onAir")).toBe("🔴 ON AIR");
    expect(I18N._t("ja", "list.onAir")).toBe("🔴 ON AIR");
    expect(I18N._t("ko", "nav.toList")).toBe("회의 목록");
    expect(I18N._t("en", "nav.toList")).toBe("Meeting list");
  });
});

describe("I18N.apply (DOM 적용)", () => {
  it("?lang 로케일로 data-i18n / data-i18n-ph 를 채움", () => {
    const I18N = load("http://localhost/?lang=ja");
    document.body.innerHTML =
      '<h1 data-i18n="gate.enter"></h1>' +
      '<input data-i18n-ph="gate.pwPlaceholder" />';
    I18N.apply(document);
    expect(document.querySelector("h1")!.textContent).toBe("入る");
    expect(document.querySelector("input")!.getAttribute("placeholder")).toBe("パスワード");
    // documentElement.lang 은 init()(load() 의 IIFE eval 시 자동 실행)이 설정한다 — apply() 가 아니라 init 의 부수효과 검증.
    expect(document.documentElement.lang).toBe("ja");
  });
});
