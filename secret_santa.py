import random
from typing import List, Dict, Tuple

def generate_secret_santa_matches(user_ids: List[int]) -> List[Tuple[int, int]]:
    """
    Kullanıcı ID'leri listesinden Yılbaşı Gizli Hediyeleşme eşleşmesi üretir.
    
    Kurallar:
    1. Herkes tam olarak 1 kişiye hediye alır.
    2. Herkese tam olarak 1 kişiden hediye gelir.
    3. Kimse kendine hediye alamaz (derangement).
    4. Tam döngü (Single Cycle / Hamiltonian Cycle) yöntemi kullanılarak
       kimsenin dışarıda kalmaması ve 2 kişilik kapalı döngülerin (A->B, B->A)
       engellenmesi (N > 2 için) sağlanır.
       
    Dönüş: List of (giver_id, receiver_id)
    """
    n = len(user_ids)
    if n < 2:
        raise ValueError("Çekiliş için en az 2 katılımcı gereklidir.")
    
    # Listeyi karıştırıyoruz
    shuffled = list(user_ids)
    random.shuffle(shuffled)
    
    # Döngüsel eşleştirme: 0 -> 1 -> 2 -> ... -> n-1 -> 0
    matches = []
    for i in range(n):
        giver = shuffled[i]
        receiver = shuffled[(i + 1) % n]
        matches.append((giver, receiver))
        
    # Güvenlik kontrolü
    for giver, receiver in matches:
        assert giver != receiver, "Hata: Birisi kendine çıktı!"
    
    givers = {g for g, _ in matches}
    receivers = {r for _, r in matches}
    assert len(givers) == n and len(receivers) == n, "Hata: Eşleşmeler birebir değil!"
    
    return matches
