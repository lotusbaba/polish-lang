const database = () => new Promise((resolve, reject) => {
  const request = indexedDB.open('polish-studio', 1);
  request.onupgradeneeded = () => request.result.createObjectStore('documents');
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});
export async function loadDocument(key) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('documents'); const req = tx.objectStore('documents').get(key);
    req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);
    tx.oncomplete = () => db.close();
  });
}
export async function storeDocument(key, value) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('documents', 'readwrite'); tx.objectStore('documents').put(value, key);
    tx.oncomplete = () => { db.close(); resolve(); }; tx.onerror = () => { db.close(); reject(tx.error); };
  });
}
