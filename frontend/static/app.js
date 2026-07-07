const statusDot = document.querySelector("#statusDot");
const healthStatus = document.querySelector("#healthStatus");

async function checkHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    statusDot.classList.add("ok");
    healthStatus.textContent = `Backend работает: ${data.service} ${data.version}`;
  } catch (error) {
    statusDot.classList.add("fail");
    healthStatus.textContent = "Backend пока не отвечает";
  }
}

checkHealth();
