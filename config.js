
// Backend URL (no trailing slash)
window.TELEADS_API = "https://sakibulislam2999.pythonanywhere.com";

// DEBUG: asol error screen e dekhabe. Kaj shesh hole ei part muche dio.
fetch(window.TELEADS_API + "/", { mode: "cors" })
  .then(function (r) { alert("Backend reached. Status: " + r.status); })
  .catch(function (e) { alert("Fetch failed: " + e.message); });
