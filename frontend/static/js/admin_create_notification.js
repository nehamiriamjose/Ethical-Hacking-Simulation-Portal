document.addEventListener("DOMContentLoaded", () => {
  const toggleBtn = document.getElementById("toggleFormBtn");
  const form = document.getElementById("notificationForm");

  if (toggleBtn && form) {
    toggleBtn.addEventListener("click", () => {
      form.classList.toggle("show");
    });
  }

  if (!form) return;

  form.addEventListener("submit", (e) => {
    const title = form.querySelector("input[name='title']").value.trim();
    const message = form.querySelector("textarea[name='message']").value.trim();

    if (!title || !message) {
      e.preventDefault();
      alert("All fields are required");
    }
  });
});
