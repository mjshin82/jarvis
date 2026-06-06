// HTML 을 텍스트로 import 하는 wrangler/esbuild 패턴.
declare module "*.html" {
  const content: string;
  export default content;
}

// PNG 을 바이너리(ArrayBuffer)로 import.
declare module "*.png" {
  const content: ArrayBuffer;
  export default content;
}

// i18n.js 를 텍스트로 import (공용 i18n 시스템, /i18n.js 로 서빙).
declare module "*/i18n.js" {
  const content: string;
  export default content;
}
