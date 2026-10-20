'use strict';
let accountsPage = 1;
let mailPage = 1;
let accountActionToken = null;
const memberRoles = {
  business_developer: 'Représentant entreprise',
  ops_manager: 'Membre de l’équipe',
  admin: 'Administrateur',
};
function publicAccountView(view) {
  for (const id of ['login', 'forgot', 'access']) $(id + '-view').hidden = id !== view;
  $('workspace').hidden = true;
}
bind('forgot-password', 'click', () => {
  $('forgot-result').textContent = '';
  publicAccountView('forgot');
});
bind('forgot-back', 'click', () => publicAccountView('login'));
bind('access-back', 'click', () => {
  accountActionToken = null;
  publicAccountView('login');
});
bind('forgot-form', 'submit', async () => {
  const data = await api(
    '/auth/forgot-password',
    { method: 'POST', body: JSON.stringify({ email: $('forgot-email').value }) },
    false,
  );
  $('forgot-result').textContent = data.message;
});
bind('access-form', 'submit', async () => {
  if (!accountActionToken) throw new Error('Rouvrez le lien reçu pour choisir votre mot de passe.');
  if ($('access-password').value !== $('access-confirm').value)
    throw new Error('Les mots de passe ne correspondent pas.');
  const data = await api(
    '/auth/accept-action',
    {
      method: 'POST',
      body: JSON.stringify({ token: accountActionToken, password: $('access-password').value }),
    },
    false,
  );
  accountActionToken = null;
  $('access-form').reset();
  publicAccountView('login');
  notify(data.message);
});
bind('change-password-form', 'submit', async () => {
  if ($('new-account-password').value !== $('confirm-account-password').value)
    throw new Error('Les mots de passe ne correspondent pas.');
  const data = await api(
    '/auth/change-password',
    {
      method: 'POST',
      body: JSON.stringify({
        current_password: $('current-password').value,
        password: $('new-account-password').value,
      }),
    },
    false,
  );
  logout();
  notify(data.message);
});
bind('open-users', 'click', () => switchView('users'));
bind('user-form', 'submit', async () => {
  if (!state.company) throw new Error('Sélectionnez une entreprise avant d’inviter son équipe.');
  const data = await api(
    '/invitations',
    {
      method: 'POST',
      body: JSON.stringify({
        email: $('new-email').value,
        role: $('new-role').value,
        company_id: Number(state.company),
      }),
    },
    false,
  );
  $('user-form').reset();
  notify(
    data.delivery_mode === 'local'
      ? 'Invitation créée en mode test local : consultez le message ci-dessous. Aucun email réel n’a été envoyé.'
      : 'Invitation enregistrée. Consultez le statut de l’envoi ci-dessous.',
  );
  accountsPage = 1;
  mailPage = 1;
  await loadMembers();
});
async function loadMembers() {
  if (state.user?.role !== 'admin') return;
  const company = state.company;
  const scope = company ? `&company_id=${company}` : '';
  $('members-list').replaceChildren();
  $('mail-list').replaceChildren();
  const [data, messages] = await Promise.all([
    api(`/users?page=${accountsPage}${scope}`, {}, false),
    api(`/mail/outbox?page=${mailPage}${scope}`, {}, false),
  ]);
  if (company !== state.company || state.user?.role !== 'admin') return;
  $('members-page').textContent = `Page ${accountsPage} · ${data.total} utilisateur(s)`;
  $('members-previous').disabled = accountsPage === 1;
  $('members-next').disabled = accountsPage * 20 >= data.total;
  for (const user of data.items) {
    const card = node('article', undefined, 'panel member-card');
    card.dataset.userId = user.id;
    card.append(
      node('h3', user.email),
      node(
        'p',
        memberRoles[user.role] +
          ' · ' +
          (user.invitation_pending
            ? 'Invitation en attente'
            : user.is_active
              ? 'Accès actif'
              : 'Accès désactivé'),
      ),
    );
    if (user.company_id)
      card.append(
        node(
          'p',
          state.companies.find((c) => c.id === user.company_id)?.name ||
            'Entreprise ' + user.company_id,
          'muted',
        ),
      );
    if (user.role === 'admin') {
      card.append(node('p', 'Le titulaire gère son mot de passe dans Mon compte.', 'muted'));
      $('members-list').append(card);
      continue;
    }
    if (user.invitation_pending) {
      const resend = node('button', 'Renvoyer l’invitation', 'secondary');
      resend.addEventListener('click', async () => {
        resend.disabled = true;
        try {
          await api(`/users/${user.id}/resend-invitation`, { method: 'POST' }, false);
          await loadMembers();
        } catch (error) {
          notify(error.message);
          resend.disabled = false;
        }
      });
      const cancel = node('button', 'Annuler l’invitation', 'secondary');
      cancel.addEventListener('click', async () => {
        cancel.disabled = true;
        try {
          await api(
            `/users/${user.id}`,
            { method: 'PATCH', body: JSON.stringify({ is_active: false, role: user.role }) },
            false,
          );
          await loadMembers();
        } catch (error) {
          notify(error.message);
          cancel.disabled = false;
        }
      });
      card.append(resend, cancel);
    } else {
      const form = node('form', undefined, 'member-edit');
      const roleLabel = node('label', 'Rôle du membre');
      const role = node('select');
      for (const key of ['business_developer', 'ops_manager']) {
        const option = node('option', memberRoles[key]);
        option.value = key;
        role.append(option);
      }
      role.value = user.role;
      roleLabel.append(role);
      const activeLabel = node('label', 'Accès actif', 'checkbox');
      const active = node('input');
      active.type = 'checkbox';
      active.checked = user.is_active;
      activeLabel.append(active);
      const save = node('button', 'Enregistrer les accès');
      save.type = 'submit';
      form.append(roleLabel, activeLabel, save);
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        save.disabled = true;
        try {
          await api(
            `/users/${user.id}`,
            {
              method: 'PATCH',
              body: JSON.stringify({ is_active: active.checked, role: role.value }),
            },
            false,
          );
          await loadMembers();
        } catch (error) {
          notify(error.message);
          save.disabled = false;
        }
      });
      card.append(form);
      if (user.is_active) {
        const reset = node('button', 'Envoyer un lien de réinitialisation', 'secondary');
        reset.addEventListener('click', async () => {
          reset.disabled = true;
          try {
            await api(`/users/${user.id}/reset-password`, { method: 'POST' }, false);
            await loadMembers();
          } catch (error) {
            notify(error.message);
            reset.disabled = false;
          }
        });
        card.append(reset);
      }
    }
    $('members-list').append(card);
  }
  $('mail-mode-info').textContent =
    messages.mode === 'local'
      ? 'Boîte de test locale : aucun email réel n’est envoyé. Les liens personnels ci-dessous permettent de tester les accès. Configurez SMTP pour les envoyer aux destinataires.'
      : 'Envoi SMTP : vérifiez le statut de chaque message. Une remise au serveur SMTP ne garantit pas sa réception dans la boîte du destinataire.';
  $('mail-page').textContent = 'Page ' + mailPage;
  $('mail-previous').disabled = mailPage === 1;
  $('mail-next').disabled = !messages.has_next;
  const mailStatuses = {
    local: 'Test local — non envoyé',
    pending: 'En attente d’envoi',
    sending: 'Envoi en cours / à vérifier',
    sent: 'Remis au serveur SMTP',
    failed: 'Envoi échoué',
    cancelled: 'Lien annulé',
  };
  for (const message of messages.items) {
    const card = node('article', undefined, 'mail-card');
    card.dataset.mailId = message.id;
    card.append(
      node('h3', message.subject),
      node('p', message.recipient),
      node('p', mailStatuses[message.status] || message.status),
      node(
        'p',
        message.usable
          ? 'Lien valable jusqu’au ' + new Date(message.expires_at).toLocaleString('fr-FR')
          : 'Lien expiré, utilisé ou remplacé.',
        'muted',
      ),
    );
    if (message.error) card.append(node('p', message.error));
    if (message.body) {
      const match = message.body.match(/https?:\/\/[^\s]+/);
      if (match) {
        const link = node('a', 'Ouvrir le lien de test');
        link.href = match[0];
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        card.append(link);
      }
    }
    if (
      messages.mode === 'smtp' &&
      message.usable &&
      ['pending', 'failed'].includes(message.status) &&
      message.attempts < 5
    ) {
      const retry = node('button', 'Réessayer l’envoi', 'secondary');
      retry.addEventListener('click', async () => {
        retry.disabled = true;
        try {
          await api(`/mail/outbox/${message.id}/retry`, { method: 'POST' }, false);
          await loadMembers();
        } catch (error) {
          notify(error.message);
          retry.disabled = false;
        }
      });
      card.append(retry);
    }
    $('mail-list').append(card);
  }
  if (!messages.items.length)
    $('mail-list').append(node('p', 'Aucun message d’accès pour cette sélection.', 'muted'));
}
bind('members-previous', 'click', async () => {
  accountsPage--;
  await loadMembers();
});
bind('members-next', 'click', async () => {
  accountsPage++;
  await loadMembers();
});
bind('mail-previous', 'click', async () => {
  mailPage--;
  await loadMembers();
});
bind('mail-next', 'click', async () => {
  mailPage++;
  await loadMembers();
});
function openAccountLink() {
  if (!location.hash.startsWith('#account=')) return;
  accountActionToken = new URLSearchParams(location.hash.slice(1)).get('account');
  history.replaceState(null, '', location.pathname + location.search);
  logout();
  $('access-form').reset();
  publicAccountView('access');
}
window.addEventListener('hashchange', openAccountLink);
openAccountLink();
