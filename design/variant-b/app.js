document.querySelectorAll(".mk").forEach((mark, i) => mark.style.setProperty("--i", i));

const ask = document.querySelector(".ask");
if (ask) {
  const input = ask.querySelector("input");
  const question = ask.querySelector(".answer .q");
  input.addEventListener("focus", () => ask.classList.add("open"));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && input.value.trim()) question.textContent = input.value.trim();
    if (event.key === "Escape") {
      ask.classList.remove("open");
      input.blur();
    }
  });
  document.addEventListener("click", (event) => {
    if (!ask.contains(event.target)) ask.classList.remove("open");
  });
}

const next = document.querySelector(".next");
if (next) {
  next.querySelector("[data-send]").addEventListener("click", () => next.classList.add("done"));
  const draft = next.querySelector("textarea");
  draft.addEventListener("input", () => draft.classList.remove("draft"), { once: true });
}

document.querySelectorAll("[data-group]").forEach((group) => {
  group.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
  });
});

const STAGES = ["Новые", "В работе", "Ждём ответа", "Сделка", "Отказ"];
let menu = null;
function closeMenu() {
  if (menu) menu.remove();
  menu = null;
}
document.querySelectorAll(".entry .move").forEach((button) => {
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    closeMenu();
    menu = document.createElement("div");
    menu.className = "menu";
    menu.setAttribute("role", "menu");
    STAGES.forEach((stage) => {
      const item = document.createElement("button");
      item.type = "button";
      item.setAttribute("role", "menuitem");
      item.textContent = stage;
      item.addEventListener("click", closeMenu);
      menu.append(item);
    });
    button.closest(".entry").append(menu);
    menu.querySelector("button").focus();
  });
});
document.addEventListener("click", closeMenu);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); });
