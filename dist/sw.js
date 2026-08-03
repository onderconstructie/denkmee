/* Service worker van Denk mee met Mechelen. Maakt de site installeerbaar (op je beginscherm)
   en laat ze ook zonder verbinding werken. GEEN cookies, GEEN trackers: enkel een lokale cache
   op je eigen toestel, die niets naar buiten stuurt. Netwerk-eerst, dus online zie je altijd de
   verse versie; offline valt hij terug op wat je al bezocht. Zelfde patroon als asgaupaust.be. */
var CACHE = 'denkmee-v1';

self.addEventListener('install', function(e){
  e.waitUntil(caches.open(CACHE).then(function(c){ return c.addAll(['/', '/techniek/']); }).catch(function(){}));
  self.skipWaiting();
});

self.addEventListener('activate', function(e){
  e.waitUntil(caches.keys().then(function(ks){
    return Promise.all(ks.map(function(k){ if(k !== CACHE) return caches.delete(k); }));
  }));
  self.clients.claim();
});

self.addEventListener('fetch', function(e){
  if(e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).then(function(resp){
      try{
        if(resp && resp.ok && new URL(e.request.url).origin === self.location.origin){
          var cp = resp.clone();
          caches.open(CACHE).then(function(c){ c.put(e.request, cp); });
        }
      }catch(err){}
      return resp;
    }).catch(function(){
      return caches.match(e.request).then(function(r){ return r || caches.match('/'); });
    })
  );
});
