// Подсветка результатов поиска
function highlightSearchResults() {
    const urlParams = new URLSearchParams(window.location.search);
    const query = urlParams.get('q');

    if (query) {
        const regex = new RegExp(escapeRegExp(query), 'gi');
        const containers = document.querySelectorAll('.searchable-text');

        containers.forEach(container => {
            const text = container.textContent;
            const highlighted = text.replace(regex, match =>
                `<span class="search-highlight">${match}</span>`
            );
            container.innerHTML = highlighted;
        });
    }
}

function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Инициализация при загрузке
document.addEventListener('DOMContentLoaded', function() {
    highlightSearchResults();

    // Сохранение состояния фильтров
    const urlParams = new URLSearchParams(window.location.search);
    const filter = urlParams.get('filter');
    const sort = urlParams.get('sort');

    if (filter) {
        document.querySelectorAll('.filter-option').forEach(option => {
            if (option.dataset.filter === filter) {
                option.classList.add('active');
                document.getElementById('current-filter').textContent = option.textContent;
            } else {
                option.classList.remove('active');
            }
        });
        document.getElementById('searchFilter').value = filter;
    }

    if (sort) {
        document.getElementById('sort').value = sort;
    }

    // Быстрый поиск при вводе
    const quickSearch = document.getElementById('quickSearch');
    if (quickSearch) {
        quickSearch.addEventListener('input', function() {
            const query = this.value.trim().toLowerCase();
            const cards = document.querySelectorAll('.book-card');

            cards.forEach(card => {
                const title = card.querySelector('.card-title').textContent.toLowerCase();
                const author = card.querySelector('.card-author').textContent.toLowerCase();
                card.style.display = (title.includes(query) || author.includes(query)) ? '' : 'none';
            });
        });
    }

    // Быстрый поиск в библиотеке
    const quickSearchBtn = document.getElementById('quickSearchBtn');
    if (quickSearchBtn) {
        quickSearchBtn.addEventListener('click', function() {
            const query = document.getElementById('quickSearch').value.trim().toLowerCase();
            const cards = document.querySelectorAll('.book-card');

            cards.forEach(card => {
                const title = card.querySelector('.card-title').textContent.toLowerCase();
                const author = card.querySelector('.card-author').textContent.toLowerCase();
                const matches = title.includes(query) || author.includes(query);
                card.style.display = matches ? '' : 'none';
            });
        });
    }
});