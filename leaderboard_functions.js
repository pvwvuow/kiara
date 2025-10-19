// ==================== LEADERBOARD PAGE FUNCTIONS ====================

// ✅ FORMAT NUMBERS (1K, 1M, etc.)
function formatNumber(num) {
    if (num === null || num === undefined) return '0';
    
    const value = Number(num);
    if (isNaN(value)) return '0';
    
    if (value < 1000) {
        return value.toString();
    }
    
    if (value < 1000000) {
        const k = value / 1000;
        if (k === Math.floor(k)) {
            return k + 'K';
        }
        return k.toFixed(1).replace('.0', '') + 'K';
    }
    
    if (value < 1000000000) {
        const m = value / 1000000;
        if (m === Math.floor(m)) {
            return m + 'M';
        }
        return m.toFixed(1).replace('.0', '') + 'M';
    }
    
    const b = value / 1000000000;
    if (b === Math.floor(b)) {
        return b + 'B';
    }
    return b.toFixed(1).replace('.0', '') + 'B';
}

// ✅ VALIDATION FUNCTIONS
function validateNumber(value, defaultValue = 0) {
    if (value === null || value === undefined) return defaultValue;
    const num = Number(value);
    if (isNaN(num)) return defaultValue;
    return Math.max(0, Math.floor(num));
}

function validateString(value, defaultValue = '') {
    if (value === null || value === undefined) return defaultValue;
    if (typeof value !== 'string') return String(value || defaultValue);
    return value.trim();
}

function validateArray(value) {
    if (!value) return [];
    if (!Array.isArray(value)) return [];
    return value;
}

function getUserNameFromData(data) {
    const possibleNameFields = [
        'name', 'displayName', 'username', 'userName', 
        'fullName', 'firstName', 'nickname'
    ];

    for (const field of possibleNameFields) {
        const name = validateString(data[field]);
        if (name && name.length > 0) {
            return name;
        }
    }

    const email = validateString(data.email);
    if (email && email.includes('@')) {
        return email.split('@')[0];
    }

    if (data.uid) {
        return `کاربر ${String(data.uid).slice(0, 6)}`;
    }

    return 'کاربر ناشناس';
}

// نمایش صفحه leaderboard
async function showLeaderboard(element) {
    try {
        Logger.log('🏆 نمایش صفحه رتبه‌بندی');
        
        // تغییر منوی فعال
        document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
        if (element) element.classList.add('active');
        
        // انیمیشن تغییر صفحه
        const dashboardPage = document.getElementById('dashboardPage');
        const achievementsPage = document.getElementById('achievementsPage');
        const learningPage = document.getElementById('learningPage');
        const leaderboardPage = document.getElementById('leaderboardPage');
        
        // مخفی کردن سایر صفحات
        dashboardPage.classList.add('slide-left');
        achievementsPage.classList.remove('slide-in');
        learningPage.classList.remove('slide-in');
        
        // نمایش صفحه leaderboard
        leaderboardPage.classList.add('slide-in');
        
        currentPage = 'leaderboard';
        
        // بارگذاری محتوا
        await loadLeaderboardData();
        
    } catch (error) {
        Logger.error('❌ خطا در نمایش صفحه رتبه‌بندی', error);
    }
}

// بارگذاری داده‌های leaderboard
async function loadLeaderboardData() {
    try {
        Logger.log('📊 شروع بارگذاری رتبه‌بندی...');
        
        if (!db) {
            throw new Error('Database not initialized');
        }

        const snapshot = await db.collection('users').get();

        if (snapshot.empty) {
            showLeaderboardEmpty();
            return;
        }

        const leaderboard = [];
        
        snapshot.forEach(doc => {
            try {
                const data = doc.data();
                
                const name = getUserNameFromData(data);
                const photoURL = validateString(data.photoURL || data.photo || data.avatar);
                
                let score = 0;
                if (data.score !== undefined) {
                    score = validateNumber(data.score);
                } else if (data.totalScore !== undefined) {
                    score = validateNumber(data.totalScore);
                } else if (data.points !== undefined) {
                    score = validateNumber(data.points);
                }
                
                let wordsCount = 0;
                if (data.words !== undefined) {
                    wordsCount = validateArray(data.words).length;
                } else if (data.learnedWords !== undefined) {
                    wordsCount = validateArray(data.learnedWords).length;
                }
                
                let streak = 0;
                if (data.streak !== undefined) {
                    streak = validateNumber(data.streak);
                } else if (data.currentStreak !== undefined) {
                    streak = validateNumber(data.currentStreak);
                }
                
                let quizzes = 0;
                if (data.quizzes !== undefined) {
                    quizzes = validateNumber(data.quizzes);
                } else if (data.totalQuizzes !== undefined) {
                    quizzes = validateNumber(data.totalQuizzes);
                }
                
                if (score === 0 && (wordsCount > 0 || quizzes > 0 || streak > 0)) {
                    score = (wordsCount * 10) + (quizzes * 50) + (streak * 20);
                }
                
                if (score > 0 || wordsCount > 0 || quizzes > 0 || streak > 0) {
                    leaderboard.push({
                        id: doc.id,
                        name: name,
                        photoURL: photoURL || null,
                        score: score,
                        wordsCount: wordsCount,
                        streak: streak,
                        quizzes: quizzes
                    });
                }
                
            } catch (userError) {
                Logger.error('❌ خطا در پردازش کاربر', userError);
            }
        });

        leaderboard.sort((a, b) => b.score - a.score);
        const top50 = leaderboard.slice(0, 50);

        Logger.success(`✅ تعداد کل کاربران فعال: ${leaderboard.length}`);
        
        displayLeaderboardData(top50);
        
    } catch (error) {
        Logger.error('❌ خطا در بارگذاری رتبه‌بندی', error);
        showLeaderboardError(error.message);
    }
}

// نمایش داده‌های leaderboard
function displayLeaderboardData(leaderboard) {
    if (!leaderboard || leaderboard.length === 0) {
        showLeaderboardEmpty();
        return;
    }

    let html = '';

    // TOP 3 PODIUM
    if (leaderboard.length >= 3) {
        html += '<div class="podium-section">';
        html += '<h2 class="podium-title">🏆 سه نفر برتر 🏆</h2>';
        html += '<div class="podium-container">';

        const top3 = [
            { user: leaderboard[1], place: 'second', medal: '🥈' },
            { user: leaderboard[0], place: 'first', medal: '🥇' },
            { user: leaderboard[2], place: 'third', medal: '🥉' }
        ];

        top3.forEach(({ user, place, medal }) => {
            if (!user) return;

            const photoURL = user.photoURL && 
                            user.photoURL !== '' && 
                            user.photoURL !== 'null' &&
                            !user.photoURL.includes('placeholder') 
                ? user.photoURL 
                : null;

            const avatarHTML = photoURL 
                ? `<img src="${photoURL}" class="podium-avatar" alt="${user.name}" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                   <div class="podium-avatar-placeholder" style="display: none;">${user.name.charAt(0).toUpperCase()}</div>`
                : `<div class="podium-avatar-placeholder">${user.name.charAt(0).toUpperCase()}</div>`;

            html += `
                <div class="podium-place ${place}">
                    ${place === 'first' ? '<div class="crown">👑</div>' : ''}
                    <div class="medal">${medal}</div>
                    ${avatarHTML}
                    <div class="podium-name">${user.name}</div>
                    <div class="podium-score">${formatNumber(user.score)}</div>
                    <div class="podium-label">امتیاز کل</div>
                </div>
            `;
        });

        html += '</div></div>';
    }

    // TABLE
    const tableUsers = leaderboard.length > 3 ? leaderboard.slice(3) : leaderboard;
    
    if (tableUsers.length > 0) {
        const tableTitle = leaderboard.length > 3 ? '🎖️ رتبه‌های 4 به بعد' : '📊 جدول رتبه‌بندی';
        const startRank = leaderboard.length > 3 ? 4 : 1;
        
        html += '<div class="table-section">';
        html += `<h2 class="table-title">${tableTitle}</h2>`;
        html += `
            <table class="leaderboard-table">
                <thead>
                    <tr>
                        <th>رتبه</th>
                        <th style="text-align: right;">کاربر</th>
                        <th>امتیاز</th>
                        <th>لغات</th>
                        <th>استرایک</th>
                    </tr>
                </thead>
                <tbody>
        `;

        tableUsers.forEach((user, index) => {
            const rank = index + startRank;

            const photoURL = user.photoURL && 
                            user.photoURL !== '' && 
                            user.photoURL !== 'null' &&
                            !user.photoURL.includes('placeholder')
                ? user.photoURL 
                : null;

            const avatarHTML = photoURL 
                ? `<img src="${photoURL}" class="table-avatar" alt="${user.name}" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                   <div class="table-avatar-placeholder" style="display: none;">${user.name.charAt(0).toUpperCase()}</div>`
                : `<div class="table-avatar-placeholder">${user.name.charAt(0).toUpperCase()}</div>`;

            html += `
                <tr>
                    <td><div class="rank-number">${rank}</div></td>
                    <td>
                        <div class="user-cell">
                            ${avatarHTML}
                            <div class="user-name-table">${user.name}</div>
                        </div>
                    </td>
                    <td><div class="stat-value">${formatNumber(user.score)}</div></td>
                    <td><div class="stat-value">${formatNumber(user.wordsCount)}</div></td>
                    <td><div class="stat-value streak">${formatNumber(user.streak)}</div></td>
                </tr>
            `;
        });

        html += '</tbody></table></div>';
    }

    document.getElementById('leaderboardContent').innerHTML = html;
}

function showLeaderboardEmpty() {
    document.getElementById('leaderboardContent').innerHTML = `
        <div class="leaderboard-empty-state">
            <div class="leaderboard-empty-icon">🏆</div>
            <h2 class="leaderboard-empty-title">هنوز کسی در رتبه‌بندی نیست!</h2>
            <p class="leaderboard-empty-text">اولین نفری باشید که قهرمان می‌شود 🌟</p>
        </div>
    `;
}

function showLeaderboardError(message = '') {
    document.getElementById('leaderboardContent').innerHTML = `
        <div class="leaderboard-empty-state">
            <div class="leaderboard-empty-icon">❌</div>
            <h2 class="leaderboard-empty-title">خطا در بارگذاری</h2>
            <p class="leaderboard-empty-text">${message || 'لطفاً صفحه را رفرش کنید'}</p>
        </div>
    `;
}
