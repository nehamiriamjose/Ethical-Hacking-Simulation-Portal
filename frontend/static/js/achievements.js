// CHANGE THIS VALUE LATER FROM BACKEND
const userXP = parseInt(document.body.dataset.xp || "0");

// XP requirements
const achievements = [
  { id: "first-blood", xp: 50 },
  { id: "speed-demon", xp: 100 },
  { id: "sql-master", xp: 200 },
  { id: "week-warrior", xp: 150 },
  { id: "perfect-score", xp: 250 },
  { id: "tutorial-master", xp: 100 },
  { id: "elite-hacker", xp: 500 },
  { id: "binary-wizard", xp: 300 },
  { id: "top-10", xp: 1000 }
];

achievements.forEach(a => {
  const card = document.getElementById(a.id);
  if (!card) return;

  if (userXP >= a.xp) {
    card.classList.remove("locked");
    card.classList.add("unlocked");
    card.querySelector(".status").innerText = "Unlocked";
  }
});
