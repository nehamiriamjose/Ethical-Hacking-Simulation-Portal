const createBtn = document.getElementById("createBtn");
const editor = document.getElementById("editor");
const tutorialList = document.getElementById("tutorialList");

createBtn.addEventListener("click", () => {
    editor.classList.toggle("show-editor");
});
// Publish tutorial function
async function publishTutorial(tutorialId) {
    try {
        console.log("Publishing tutorial ID:", tutorialId);
        const res = await fetch(`/admin/tutorials/${tutorialId}/publish`, {
            method: "POST",
            headers: { "Content-Type": "application/json" }
        });
        
        const data = await res.json();
        console.log("Publish response:", data);
        
        if (data.success) {
            alert("✅ Tutorial Published Successfully!");
            // Reload the tutorials list to reflect changes
            location.reload();
        } else {
            alert("❌ Error: " + (data.error || "Failed to publish tutorial"));
        }
    } catch (err) {
        console.error("Publish error:", err);
        alert("❌ Error publishing tutorial: " + err.message);
    }
}

// Unpublish tutorial function
async function unpublishTutorial(tutorialId) {
    try {
        const res = await fetch(`/admin/tutorials/${tutorialId}/unpublish`, {
            method: "POST",
            headers: { "Content-Type": "application/json" }
        });
        
        const data = await res.json();
        
        if (data.success) {
            alert("Tutorial unpublished successfully!");
            // Reload the tutorials list to reflect changes
            location.reload();
        } else {
            alert("Error: " + (data.error || "Failed to unpublish tutorial"));
        }
    } catch (err) {
        console.error("Unpublish error:", err);
        alert("Error unpublishing tutorial: " + err.message);
    }
}