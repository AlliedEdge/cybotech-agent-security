/**
 * Vitest global test setup.
 * Runs before every test file.
 */

import "@testing-library/jest-dom";

// ---------------------------------------------------------------------------
// File.text() polyfill — jsdom's File may not implement .text() in all versions.
// ---------------------------------------------------------------------------
if (typeof File !== "undefined" && !File.prototype.text) {
  File.prototype.text = function () {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(reader.error);
      reader.readAsText(this);
    });
  };
}
