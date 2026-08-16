import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

if (!("DOMMatrix" in globalThis)) {
  Object.defineProperty(globalThis, "DOMMatrix", { value: class DOMMatrix {} });
}
if (!("Path2D" in globalThis)) {
  Object.defineProperty(globalThis, "Path2D", { value: class Path2D {} });
}
if (!("ImageData" in globalThis)) {
  Object.defineProperty(globalThis, "ImageData", { value: class ImageData {} });
}

afterEach(cleanup);
