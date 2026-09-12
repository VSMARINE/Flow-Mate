// ==============================
// POMODORO TIMER
// ==============================


// ==============================
// DOM ELEMENTS
// ==============================

const timer = document.getElementById("timer");
const mode = document.getElementById("mode");
const quote = document.getElementById("quote");
const notification = document.getElementById("notification");

const progressCircle = document.getElementById("progressCircle");

const startBtn = document.getElementById("startBtn");
const pauseBtn = document.getElementById("pauseBtn");
const resetBtn = document.getElementById("resetBtn");

const sessionCount = document.getElementById("sessionCount");
const focusTime = document.getElementById("focusTime");
const timerLabel = document.getElementById("timerLabel");


// ==============================
// TIMER SETTINGS
// ==============================

const STUDY_TIME = 10;
const BREAK_TIME = 5;


// ==============================
// CIRCULAR PROGRESS
// ==============================

const CIRCLE_RADIUS = 120;

const CIRCLE_CIRCUMFERENCE =
    2 * Math.PI * CIRCLE_RADIUS;


// ==============================
// VARIABLES
// ==============================

let totalTime = STUDY_TIME;
let timeLeft = STUDY_TIME;

let isStudy = true;
let running = false;

let completedSessions = 0;
let totalFocusMinutes = 0;

let interval = null;


// ==============================
// MOTIVATIONAL QUOTES
// ==============================

const quotes = [

    "Success is built one focused session at a time.",

    "Discipline beats motivation.",

    "Small progress every day leads to big success.",

    "Stay focused. Ignore distractions.",

    "Your future self will thank you.",

    "One session at a time.",

    "Focus on progress, not perfection."

];


// ==============================
// UPDATE TIMER DISPLAY
// ==============================

function updateDisplay() {

    const minutes =
        Math.floor(timeLeft / 60);

    const seconds =
        timeLeft % 60;


    timer.innerHTML =
        String(minutes).padStart(2, "0") +
        ":" +
        String(seconds).padStart(2, "0");


    const percentage =
        (timeLeft / totalTime) * 100;


    const offset =
        CIRCLE_CIRCUMFERENCE -
        (percentage / 100) *
        CIRCLE_CIRCUMFERENCE;


    progressCircle.style.strokeDasharray =
        CIRCLE_CIRCUMFERENCE;

    progressCircle.style.strokeDashoffset =
        offset;

}


// ==============================
// NOTIFICATION
// ==============================

function showNotification(message) {

    notification.style.display = "block";

    notification.innerHTML = message;


    setTimeout(function () {

        notification.style.display = "none";

    }, 3000);

}


// ==============================
// CHANGE QUOTE
// ==============================

function changeQuote() {

    const random =
        Math.floor(
            Math.random() * quotes.length
        );

    quote.innerHTML =
        quotes[random];

}


// ==============================
// CLICK SOUND
// ==============================

function playClickSound() {

    const AudioContextClass =
        window.AudioContext ||
        window.webkitAudioContext;


    if (!AudioContextClass) {
        return;
    }


    const audioContext =
        new AudioContextClass();


    const oscillator =
        audioContext.createOscillator();

    const gain =
        audioContext.createGain();


    oscillator.connect(gain);

    gain.connect(
        audioContext.destination
    );


    oscillator.frequency.value = 500;


    gain.gain.setValueAtTime(
        0.08,
        audioContext.currentTime
    );


    oscillator.start();


    gain.gain.exponentialRampToValueAtTime(
        0.001,
        audioContext.currentTime + 0.08
    );


    oscillator.stop(
        audioContext.currentTime + 0.08
    );

}


// ==============================
// COMPLETION SOUND
// ==============================

function playCompleteSound() {

    const AudioContextClass =
        window.AudioContext ||
        window.webkitAudioContext;


    if (!AudioContextClass) {
        return;
    }


    const audioContext =
        new AudioContextClass();


    const oscillator =
        audioContext.createOscillator();

    const gain =
        audioContext.createGain();


    oscillator.connect(gain);

    gain.connect(
        audioContext.destination
    );


    oscillator.frequency.value = 700;


    gain.gain.setValueAtTime(
        0.12,
        audioContext.currentTime
    );


    oscillator.start();


    oscillator.frequency.setValueAtTime(
        900,
        audioContext.currentTime + 0.2
    );


    gain.gain.exponentialRampToValueAtTime(
        0.001,
        audioContext.currentTime + 0.5
    );


    oscillator.stop(
        audioContext.currentTime + 0.5
    );

}


// ==============================
// SAVE SESSION TO DATABASE
// ==============================

function savePomodoroSession() {

    fetch("/save_pomodoro_session", {

        method: "POST",

        headers: {
            "Content-Type": "application/json"
        },

        body: JSON.stringify({

            focus_minutes: 25

        })

    })

    .then(response => response.json())

    .then(data => {

        if (data.success) {

            console.log(
                "Pomodoro session saved successfully."
            );
             // Refresh statistics and history

    loadPomodoroData();


        } else {

            console.error(
                "Session could not be saved."
            );

        }

    })

    .catch(error => {

        console.error(
            "Error saving Pomodoro session:",
            error
        );

    });

}



// ==============================
// LOAD POMODORO DATA
// ==============================

function loadPomodoroData() {

    fetch("/pomodoro_data")

    .then(response => response.json())

    .then(data => {

        if (!data.success) {

            console.error(
                "Could not load Pomodoro data."
            );

            return;

        }


        // ==========================
        // UPDATE TODAY'S STATISTICS
        // ==========================

        completedSessions =
            data.sessions;

        totalFocusMinutes =
            data.focus_minutes;


        sessionCount.innerHTML =
            completedSessions;


        focusTime.innerHTML =
            totalFocusMinutes + " Min";


        // ==========================
        // SESSION HISTORY
        // ==========================

        const historyContainer =
            document.getElementById(
                "sessionHistory"
            );


        historyContainer.innerHTML = "";


        // No sessions

        if (
            data.recent_sessions.length === 0
        ) {

            historyContainer.innerHTML = `

                <div class="alert alert-light text-center">

                    🍅 No Pomodoro sessions yet.
                    Complete your first focus session!

                </div>

            `;

            return;

        }


        // Display sessions

        data.recent_sessions.forEach(
            function (item) {

                const sessionItem =
                    document.createElement("div");


                sessionItem.className =
                    "session-history-item";

const parts =
    item.completed_at.split(" ");


const datePart =
    parts[0].split("-");


const timePart =
    parts[1].split(":");


const date = new Date(

    parseInt(datePart[0]),

    parseInt(datePart[1]) - 1,

    parseInt(datePart[2]),

    parseInt(timePart[0]),

    parseInt(timePart[1]),

    parseInt(timePart[2])

);


const today = new Date();

const yesterday = new Date();

yesterday.setDate(
    today.getDate() - 1
);


let formattedDate;


const sameDay =
    date.getDate() === today.getDate() &&
    date.getMonth() === today.getMonth() &&
    date.getFullYear() === today.getFullYear();


const isYesterday =
    date.getDate() === yesterday.getDate() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getFullYear() === yesterday.getFullYear();


const time = date.toLocaleTimeString(
    [],
    {
        hour: "2-digit",
        minute: "2-digit"
    }
);


if (sameDay) {

    formattedDate =
        "Today, " + time;

}

else if (isYesterday) {

    formattedDate =
        "Yesterday, " + time;

}

else {

    formattedDate =
        date.toLocaleDateString(
            [],
            {
                day: "numeric",
                month: "short",
                year: "numeric"
            }
        )
        + ", " + time;

}

                sessionItem.innerHTML = `

                    <div class="session-info">

                        <div class="session-icon">

                            🍅

                        </div>


                        <div>

                            <strong>

                                Focus Session

                            </strong>


                            <div class="session-date">

                                ${formattedDate}

                            </div>

                        </div>

                    </div>


                    <div class="session-minutes">

                        ${item.focus_minutes} Min

                    </div>

                `;


                historyContainer.appendChild(
                    sessionItem
                );

            }
        );

    })

    .catch(error => {

        console.error(
            "Error loading Pomodoro data:",
            error
        );

    });

}




// ==============================
// START TIMER
// ==============================

startBtn.addEventListener(
    "click",
    function () {

        playClickSound();


        if (running) {
            return;
        }


        running = true;


        interval = setInterval(
            runTimer,
            1000
        );

    }
);


// ==============================
// PAUSE TIMER
// ==============================

pauseBtn.addEventListener(
    "click",
    function () {

        playClickSound();


        clearInterval(interval);

        interval = null;

        running = false;


        showNotification(
            "⏸ Timer Paused"
        );

    }
);


// ==============================
// RESET TIMER
// ==============================

resetBtn.addEventListener(
    "click",
    function () {

        playClickSound();


        clearInterval(interval);

        interval = null;

        running = false;


        isStudy = true;

        totalTime = STUDY_TIME;

        timeLeft = STUDY_TIME;


        mode.innerHTML =
            "🍅 Study Session";


        mode.className =
            "text-center text-success fw-bold mb-4";


        progressCircle.style.stroke =
            "#198754";


        if (timerLabel) {

            timerLabel.innerHTML =
                "Focus Time";

        }


        updateDisplay();


        showNotification(
            "🔄 Timer Reset Successfully!"
        );

    }
);


// ==============================
// MAIN TIMER
// ==============================

function runTimer() {

    if (timeLeft > 0) {

        timeLeft--;

        updateDisplay();

        return;

    }


    clearInterval(interval);

    interval = null;

    running = false;


    playCompleteSound();


    // ==========================
    // STUDY SESSION COMPLETED
    // ==========================

    if (isStudy) {

        completedSessions++;

        totalFocusMinutes += 25;


        // Save completed session
        savePomodoroSession();


        sessionCount.innerHTML =
            completedSessions;


        focusTime.innerHTML =
            totalFocusMinutes + " Min";


        showNotification(
            "🎉 Study Session Completed! Break Started."
        );


        changeQuote();


        // Switch to break

        isStudy = false;

        totalTime = BREAK_TIME;

        timeLeft = BREAK_TIME;


        mode.innerHTML =
            "☕ Break Time";


        mode.className =
            "text-center text-info fw-bold mb-4";


        progressCircle.style.stroke =
            "#0dcaf0";


        if (timerLabel) {

            timerLabel.innerHTML =
                "Relax Time";

        }

    }


    // ==========================
    // BREAK COMPLETED
    // ==========================

    else {

        showNotification(
            "🍅 Break Finished! Study Session Started."
        );


        isStudy = true;

        totalTime = STUDY_TIME;

        timeLeft = STUDY_TIME;


        mode.innerHTML =
            "🍅 Study Session";


        mode.className =
            "text-center text-success fw-bold mb-4";


        progressCircle.style.stroke =
            "#198754";


        if (timerLabel) {

            timerLabel.innerHTML =
                "Focus Time";

        }

    }


    updateDisplay();


    // Automatically start next mode

    running = true;


    interval = setInterval(
        runTimer,
        1000
    );

}


// ==============================
// INITIALIZE
// ==============================

progressCircle.style.strokeDasharray =
    CIRCLE_CIRCUMFERENCE;

progressCircle.style.strokeDashoffset = 0;


updateDisplay();


sessionCount.innerHTML =
    completedSessions;


focusTime.innerHTML =
    totalFocusMinutes + " Min";


changeQuote();
loadPomodoroData();