document.querySelectorAll("nav a").forEach(a => {
  if (a.getAttribute("href") === location.pathname) a.classList.add("active");
});

function showToast(message, type) {
  const t = document.createElement("div");
  t.className = "toast" + (type ? " " + type : "");
  t.textContent = message;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2500);
}