const authForm = document.querySelector("[data-auth-form]");
let passwordResetToken = "";

if (authForm?.dataset.authForm === "reset") {
  passwordResetToken = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
  window.history.replaceState(null, document.title, window.location.pathname);
}

function getCookie(name) {
  const prefix = `${name}=`;
  const item = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

function safeReturnPath() {
  const requested = new URLSearchParams(window.location.search).get("next");
  return requested && requested.startsWith("/") && !requested.startsWith("//") && !requested.includes("\\")
    ? requested
    : "/";
}

async function postAuth(path, values) {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": getCookie("saspal_csrf"),
    },
    body: JSON.stringify(values),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(payload?.detail || "The request could not be completed.");
  }
  return payload;
}

if (authForm) {
  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = authForm.querySelector(".auth-message");
    const button = authForm.querySelector("button[type='submit']");
    const values = Object.fromEntries(new FormData(authForm));
    message.textContent = "";
    button.disabled = true;

    try {
      if (values.password && values.password_confirmation && values.password !== values.password_confirmation) {
        throw new Error("Passwords do not match.");
      }

      switch (authForm.dataset.authForm) {
        case "login":
          await postAuth("/auth/login", values);
          window.location.assign(safeReturnPath());
          break;
        case "signup":
          await postAuth("/auth/signup", values);
          window.location.assign("/");
          break;
        case "forgot":
          await postAuth("/auth/forgot-password", values);
          message.classList.add("success");
          message.textContent = "If an account matches that address, a reset link will be sent.";
          button.disabled = false;
          break;
        case "reset": {
          await postAuth("/auth/reset-password", {
            token: passwordResetToken,
            password: values.password,
            password_confirmation: values.password_confirmation,
          });
          window.location.assign("/login?reset=complete");
          break;
        }
        default:
          throw new Error("This form is unavailable.");
      }
    } catch (error) {
      message.classList.remove("success");
      message.textContent = error.message || "The request could not be completed.";
      button.disabled = false;
    }
  });
}
