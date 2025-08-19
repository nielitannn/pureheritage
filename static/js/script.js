
document.querySelectorAll('.scroll-link').forEach(link => {
    link.addEventListener('click', function(e) {
        e.preventDefault();
        const section = document.querySelector(this.getAttribute('href'));
        section.scrollIntoView({ behavior: 'smooth' });
    });
});


function showModal(monumentId) {
    const modal = document.getElementById('modal');
    // Загрузите данные для памятника через AJAX или из объекта
    modal.style.display = 'block';
}

function closeModal() {
    document.getElementById('modal').style.display = 'none';
}


async function sendForm(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    
    try {
        const response = await fetch('/send_message', {
            method: 'POST',
            body: formData
        });
        document.getElementById('form-message').textContent = await response.text();
    } catch (error) {
        console.error('Ошибка:', error);
    }
}
document.addEventListener('DOMContentLoaded', () => {
    const cards = document.querySelectorAll('.news-card');
    cards.forEach((card, index) => {
        setTimeout(() => {
            card.style.opacity = '1';
            card.style.transform = 'translateY(0)';
        }, index * 100);
    });
});
function toggleExpand(newsId) {
    const card = document.getElementById(`content-${newsId}`).parentElement;
    card.classList.toggle('expanded');
    
    // Плавная прокрутка к контенту
    if (card.classList.contains('expanded')) {
        card.scrollIntoView({ 
            behavior: 'smooth', 
            block: 'nearest'
        });
    }
}
