function edit_user(id){
  const name = document.getElementById("name")
  const email = document.getElementById("email")
  const is_Admin = document.getElementById("is_Admin")
  
  const payload = {
    "name":name.value,
    "email":email.value,
    "is_Admin":is_Admin.checked
  }
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/usuarios/editar/'+id, true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.onload = () => {
    if (xhr.status == 200){
      //const modalEl = document.getElementById('exampleModal');
      //const bsModal = new bootstrap.Modal(modalEl);
      //bsModal.show();
    
      location.href = "/usuarios"
    }
  };
  xhr.send(JSON.stringify(payload));

}
function change_passwd(userId){
  // Si ya existiera uno anterior, lo eliminamos
  const prev = document.getElementById('passwordModal');
  if (prev) prev.remove();

  // 1. Contenedor raíz del modal
  const modalEl = document.createElement('div');
  modalEl.classList.add('modal', 'fade');
  modalEl.id = 'passwordModal';
  modalEl.tabIndex = -1;

  // 2. Diálogo centrado
  const dialogEl = document.createElement('div');
  dialogEl.classList.add('modal-dialog', 'modal-dialog-centered');

  // 3. Contenido
  const contentEl = document.createElement('div');
  contentEl.classList.add('modal-content');

  // 4. Header
  const headerEl = document.createElement('div');
  headerEl.classList.add('modal-header');
  const titleEl = document.createElement('h5');
  titleEl.classList.add('modal-title');
  titleEl.textContent = 'Cambiar Contraseña';
  const btnClose = document.createElement('button');
  btnClose.type = 'button';
  btnClose.classList.add('btn-close');
  btnClose.setAttribute('data-bs-dismiss', 'modal');
  btnClose.setAttribute('aria-label', 'Cerrar');
  headerEl.append(titleEl, btnClose);

  // 5. Body con formulario
  const bodyEl = document.createElement('div');
  bodyEl.classList.add('modal-body');

  const formEl = document.createElement('form');
  formEl.id = 'formChangePassword';

  // Helper para fila label+input
  function makeRow(id, labelText, type = 'password') {
    const row = document.createElement('div');
    row.classList.add('mb-3');
    const label = document.createElement('label');
    label.setAttribute('for', id);
    label.classList.add('form-label');
    label.textContent = labelText;
    const input = document.createElement('input');
    input.classList.add('form-control');
    input.type = type;
    input.id = id;
    input.name = id;
    row.append(label, input);
    return row;
  }

  // Campos: antigua, nueva, repetir
  formEl.append(
    makeRow('old_pass',       'Contraseña Actual'),
    makeRow('new_pass',       'Nueva Contraseña'),
    makeRow('repet_new_pass', 'Repetir Nueva Contraseña')
  );

  bodyEl.appendChild(formEl);

  // 6. Footer
  const footerEl = document.createElement('div');
  footerEl.classList.add('modal-footer');

  const btnCancel = document.createElement('button');
  btnCancel.type = 'button';
  btnCancel.classList.add('btn', 'btn-secondary');
  btnCancel.setAttribute('data-bs-dismiss', 'modal');
  btnCancel.textContent = 'Cancelar';

  const btnSave = document.createElement('button');
  btnSave.type = 'button';
  btnSave.classList.add('btn', 'btn-primary');
  btnSave.textContent = 'Guardar';
  btnSave.addEventListener('click', () => submitChangePassword(userId));

  footerEl.append(btnCancel, btnSave);

  // 7. Montar todo
  contentEl.append(headerEl, bodyEl, footerEl);
  dialogEl.appendChild(contentEl);
  modalEl.appendChild(dialogEl);
  document.body.appendChild(modalEl);

  // 8. Inicializar y mostrar con Bootstrap 5
  const bsModal = new bootstrap.Modal(modalEl);
  bsModal.show();
}
function submitChangePassword(userId) {
  const form = document.getElementById('formChangePassword');
  const payload = {
    old_pass:       form.old_pass.value,
    new_pass:       form.new_pass.value,
    repet_new_pass: form.repet_new_pass.value
  };

  fetch(`/usuarios/editar/password/${userId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
    .then(res => res.json().then(body => ({ status: res.status, body })))
    .then(({ status, body }) => {
      const modalEl = document.getElementById('passwordModal');
      const modalInstance = bootstrap.Modal.getInstance(modalEl);
      const bodyEl = modalEl.querySelector('.modal-body');

      // Primero, elimina cualquier alert previa
      const prevAlert = modalEl.querySelector('.alert');
      if (prevAlert) prevAlert.remove();

      if (status >= 200 && status < 300) {
        // Éxito → cierra modal y redirige
        modalInstance.hide();
        window.location.href = '/usuarios';
      } else {
        // Error → muestra mensaje dentro del modal
        const alertEl = document.createElement('div');
        alertEl.classList.add('alert', 'alert-danger');
        alertEl.textContent = body.error || body.message || 'Error al cambiar contraseña';
        // Inserta el alert al principio del body del modal
        bodyEl.prepend(alertEl);
      }
    })
    .catch(err => {
      console.error(err);
      // Si hay error de red, también lo mostramos
      const modalEl = document.getElementById('passwordModal');
      const bodyEl = modalEl.querySelector('.modal-body');
      const alertEl = document.createElement('div');
      alertEl.classList.add('alert', 'alert-danger');
      alertEl.textContent = 'Error de red. Intenta de nuevo más tarde.';
      bodyEl.prepend(alertEl);
    });
}
