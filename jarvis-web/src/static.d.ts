// HTML 을 텍스트로 import 하는 wrangler/esbuild 패턴.
declare module "*.html" {
  const content: string;
  export default content;
}
