(function () {
  var s = null;
  try {
    s = localStorage.getItem("promptpotter.theme");
  } catch (_) {}
  if ((s || "light") === "light") document.documentElement.setAttribute("data-theme", "light");
})();
