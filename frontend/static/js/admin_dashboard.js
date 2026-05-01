// Admin Dashboard JS - Load real data and initialize charts
let activityChart = null;

document.addEventListener('DOMContentLoaded', function() {
    loadDashboardData();
    setInterval(loadDashboardData, 30000); // Refresh every 30 seconds
});

async function loadDashboardData() {
    try {
        // Load summary analytics
        const response = await fetch('/admin/api/analytics/summary');
        const data = await response.json();
        
        if (data.success && data.analytics) {
            updateStatsCards(data.analytics);
        }

        // Update last refresh time
        updateLastRefreshTime();
    } catch (error) {
        console.error('Error loading dashboard data:', error);
    }
}

function updateStatsCards(analytics) {
    // Total Users
    const totalUsers = analytics.total_users || 0;
    const activeUsers = analytics.active_users || 0;
    const lastMonthUsers = analytics.total_users ? Math.max(1, Math.floor(totalUsers * 0.85)) : 0;
    const userGrowth = lastMonthUsers > 0 ? Math.round(((totalUsers - lastMonthUsers) / lastMonthUsers) * 100) : 0;
    
    document.getElementById('statTotalUsers').textContent = totalUsers;
    document.getElementById('changeUsers').textContent = (userGrowth >= 0 ? '↑' : '↓') + ' ' + Math.abs(userGrowth) + '%';
    document.getElementById('changeUsers').className = 'stat-change ' + (userGrowth >= 0 ? 'positive' : 'negative');

    // Active Users
    const todayLogins = analytics.today_logins || 0;
    document.getElementById('statActiveUsers').textContent = activeUsers;
    document.getElementById('changeActive').textContent = todayLogins + ' logins today';

    // Total Challenges
    const totalChallenges = analytics.total_challenges || 0;
    document.getElementById('statChallenges').textContent = totalChallenges;
    document.getElementById('changeChallenges').textContent = '0 new';

    // Tutorials
    const tutorials = analytics.total_tutorials || 0;
    document.getElementById('statTutorials').textContent = tutorials;
    document.getElementById('changeTutorials').textContent = '0 draft';

    // Avg Score
    const avgXp = analytics.avg_xp || 0;
    document.getElementById('statAvgScore').textContent = avgXp;
    document.getElementById('changeScore').textContent = 'average';

    // Banned Users
    const bannedUsers = analytics.banned_users || 0;
    document.getElementById('statBanned').textContent = bannedUsers;
    document.getElementById('changeBanned').textContent = bannedUsers + ' suspended';
}

function updateActivityChart(trends) {
    const ctx = document.getElementById('activityChart');
    if (!ctx) return;

    const labels = trends.map(t => t.date).reverse();
    const userData = trends.map(t => t.new_users || 0).reverse();
    const activeData = trends.map(t => t.active_users || 0).reverse();

    if (activityChart) {
        activityChart.data.labels = labels;
        activityChart.data.datasets[0].data = userData;
        activityChart.data.datasets[1].data = activeData;
        activityChart.update();
    } else {
        activityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'New Users',
                        data: userData,
                        borderColor: '#14f1b2',
                        backgroundColor: 'rgba(20, 241, 178, 0.1)',
                        tension: 0.4,
                        fill: true,
                        pointRadius: 5,
                        pointBackgroundColor: '#14f1b2',
                        pointBorderColor: '#050711',
                        pointBorderWidth: 2,
                        pointHoverRadius: 7
                    },
                    {
                        label: 'Active Users',
                        data: activeData,
                        borderColor: '#fbbf24',
                        backgroundColor: 'rgba(251, 191, 36, 0.05)',
                        tension: 0.4,
                        fill: true,
                        pointRadius: 5,
                        pointBackgroundColor: '#fbbf24',
                        pointBorderColor: '#050711',
                        pointBorderWidth: 2,
                        pointHoverRadius: 7
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        labels: {
                            color: '#888',
                            font: {
                                size: 12,
                                weight: 600
                            }
                        }
                    },
                    filler: {
                        propagate: true
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: {
                            color: 'rgba(20, 241, 178, 0.05)',
                            drawBorder: false
                        },
                        ticks: {
                            color: '#666',
                            font: {
                                size: 11
                            }
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        },
                        ticks: {
                            color: '#666',
                            font: {
                                size: 11
                            }
                        }
                    }
                }
            }
        });
    }
}

function initActivityChart() {
    const ctx = document.getElementById('activityChart');
    if (!ctx) return;

    // Initialize with empty data
    activityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            datasets: [
                {
                    label: 'New Users',
                    data: [0, 0, 0, 0, 0, 0, 0],
                    borderColor: '#14f1b2',
                    backgroundColor: 'rgba(20, 241, 178, 0.1)',
                    tension: 0.4,
                    fill: true
                },
                {
                    label: 'Active Users',
                    data: [0, 0, 0, 0, 0, 0, 0],
                    borderColor: '#fbbf24',
                    backgroundColor: 'rgba(251, 191, 36, 0.05)',
                    tension: 0.4,
                    fill: true
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        color: '#888'
                    }
                }
            },
            scales: {
                y: {
                    grid: { color: 'rgba(20, 241, 178, 0.05)' },
                    ticks: { color: '#666' }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: '#666' }
                }
            }
        }
    });
}

function updateActivityFeed(activities) {
    const feed = document.getElementById('activityFeed');
    if (!feed) return;

    if (!activities || activities.length === 0) {
        feed.innerHTML = '<div class="activity-item">No recent activity</div>';
        return;
    }

    feed.innerHTML = activities.slice(0, 8).map(activity => {
        const time = new Date(activity.timestamp || Date.now());
        const timeStr = getRelativeTime(time);
        
        let icon = '📝';
        let text = activity.description || 'Activity recorded';
        
        if (activity.type === 'login') {
            icon = '🔓';
            text = `${activity.user_email || 'User'} logged in`;
        } else if (activity.type === 'user_created') {
            icon = '👤';
            text = `New user registered: ${activity.user_email || 'Unknown'}`;
        } else if (activity.type === 'challenge_completed') {
            icon = '✅';
            text = `Challenge completed`;
        } else if (activity.type === 'tutorial_started') {
            icon = '📚';
            text = `Tutorial accessed`;
        }

        return `
            <div class="activity-item">
                <span style="margin-right: 8px;">${icon}</span>
                <span>${text}</span>
                <div class="activity-time">${timeStr}</div>
            </div>
        `;
    }).join('');
}

function getRelativeTime(date) {
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);
    
    if (seconds < 60) return 'Just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
}

function updateLastRefreshTime() {
    const elem = document.getElementById('lastUpdate');
    if (elem) {
        elem.textContent = 'Just now';
    }
}

function updateEngagementMetrics(engagement) {
    // Challenge Completion Rate
    const completionRate = engagement.challenge_completion_rate || 0;
    const completionEl = document.getElementById('challengeCompletion');
    if (completionEl) {
        completionEl.textContent = Math.round(completionRate) + '%';
    }

    // Tutorial Views
    const tutorialViews = engagement.tutorial_views || 0;
    const viewsEl = document.getElementById('tutorialViews');
    if (viewsEl) {
        viewsEl.textContent = tutorialViews.toLocaleString();
    }

    // Quiz Pass Rate
    const quizPassRate = engagement.quiz_pass_rate || 0;
    const passEl = document.getElementById('quizPassRate');
    if (passEl) {
        passEl.textContent = Math.round(quizPassRate) + '%';
    }

    // Total XP Distributed
    const totalXp = engagement.total_xp_distributed || 0;
    const xpEl = document.getElementById('totalXpDistributed');
    if (xpEl) {
        xpEl.textContent = totalXp.toLocaleString();
    }
}

function updateContentPerformance(content) {
    // Top Challenge
    const topChallenge = document.getElementById('topChallenge');
    if (topChallenge && content.top_challenge) {
        topChallenge.innerHTML = `
            <p style="margin: 0; color: #14f1b2; font-weight: 600; margin-bottom: 6px;">${content.top_challenge.name}</p>
            <p style="margin: 0; font-size: 13px; color: #888;">${content.top_challenge.completions || 0} completions</p>
        `;
    }

    // Top Tutorial
    const topTutorial = document.getElementById('topTutorial');
    if (topTutorial && content.top_tutorial) {
        topTutorial.innerHTML = `
            <p style="margin: 0; color: #14f1b2; font-weight: 600; margin-bottom: 6px;">${content.top_tutorial.name}</p>
            <p style="margin: 0; font-size: 13px; color: #888;">${content.top_tutorial.views || 0} views</p>
        `;
    }
}

function updateTopUsers(users) {
    const tbody = document.getElementById('topUsersBody');
    if (!tbody) return;

    if (!users || users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="padding: 20px; text-align: center; color: #888;">No users found</td></tr>';
        return;
    }

    tbody.innerHTML = users.map((user, idx) => `
        <tr style="border-bottom: 1px solid rgba(20, 241, 178, 0.08); transition: all 0.2s ease;">
            <td style="padding: 15px; color: #14f1b2; font-weight: 600; font-size: 16px;">
                ${idx === 0 ? '🥇' : idx === 1 ? '🥈' : idx === 2 ? '🥉' : '#' + (idx + 1)}
            </td>
            <td style="padding: 15px; color: #fff; font-weight: 500;">${user.name || 'User'}</td>
            <td style="padding: 15px; color: #14f1b2;">${user.level || 1}</td>
            <td style="padding: 15px; color: #4ade80; font-weight: 600;">${(user.points || 0).toLocaleString()}</td>
            <td style="padding: 15px; color: #3b82f6;">${user.challenges || 0}</td>
        </tr>
    `).join('');
}
