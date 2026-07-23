// Si l'utilisateur est déjà connecté, les boutons "S'inscrire / Essayer
// gratuitement" doivent renvoyer vers l'accueil plutôt que de redemander
// une inscription. Ce script réécrit ces liens à la volée sur les pages
// qui ne gèrent pas elles-mêmes l'état de connexion (contrairement à
// index.html qui a sa propre logique).
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { SUPABASE_URL, SUPABASE_ANON_KEY } from './supabase-config.js';

const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
const { data: { session } } = await supabase.auth.getSession();

if (session) {
  document.querySelectorAll('a[href="signup.html"]').forEach((a) => {
    a.href = 'index.html';
  });
}
