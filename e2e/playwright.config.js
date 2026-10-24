module.exports = {
  testDir: ".",
  testMatch: "*.spec.js",
  use: { baseURL: "http://localhost:4173" },
  webServer: { command: "python -m http.server 4173 --directory ../frontend", url: "http://localhost:4173", reuseExistingServer: true },
};
