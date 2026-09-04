(() => {
  const key = "personal-stock-agent-theme";
  try {
    const value = window.localStorage.getItem(key);
    document.documentElement.dataset.theme = value === "light" || value === "dark" ? value : "system";
  } catch {
    document.documentElement.dataset.theme = "system";
  }
})();
