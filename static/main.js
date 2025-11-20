function login(){
  username = document.getElementById("username").value
  password = document.getElementById("password").value
  error_message = document.getElementById("error")
  if (username == "" || password == ""){
    error_message.innerText = "Error: El usuario o la password estan vacios"
    error_message.style.removeProperty("display")
  }
  const login_data = {
    "username": username,
    "password" : password
  }
  const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/login');
    xhr.setRequestHeader('Content-Type', 'application/json;charset=UTF-8');

    xhr.onreadystatechange = () => {
      if (xhr.readyState === XMLHttpRequest.DONE) {
        if (xhr.status >= 200 && xhr.status < 300) {
          console.log('Respuesta:', JSON.parse(xhr.responseText));
          window.location.assign('/dashboard');

        } else {
          if (xhr.status == 422 || xhr.status == 401){
            error_message.innerText = "Error al hacer login"
            error_message.style.removeProperty("display")
          }
        }
      }
    };

    xhr.send(JSON.stringify(login_data));

}

// Permitir submit con Enter en los campos de login
document.addEventListener('DOMContentLoaded', () => {
  const userInput = document.getElementById('username');
  const passInput = document.getElementById('password');
  [userInput, passInput].forEach(el => {
    if (!el) return;
    el.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        login();
      }
    });
  });
});
