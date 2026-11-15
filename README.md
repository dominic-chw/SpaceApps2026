# SpaceApps2026
# Planetary twins 🌎🔴

### Mars-to-Earth Drill-Site Analog Finder

**NASA Space Apps Challenge 2026**

## About the Project

How can we test equipment for Mars without actually going to Mars?

**Planetary Twin** is a Mars-to-Earth geological matching tool designed to help researchers identify locations on Earth that have similar terrain and subsurface conditions to specific regions on Mars.

Testing drilling equipment on Mars is expensive, complex, and difficult to repeat. By identifying comparable locations on Earth, researchers can explore potential testing environments before deploying equipment to another planet.

Our goal is to make the process of finding Earth analog sites more accessible through an interactive, data-driven platform.

## How It Works

Planetary Twin allows users to:

1. **Explore Mars:** Navigate an interactive 3D Mars globe and select a location using coordinates.
2. **Select a Drilling Depth:** Choose from four depth ranges, from surface conditions to deeper subsurface environments.
3. **Compare Geological Conditions:** Examine characteristics such as temperature, rock type, ground composition, ice presence, hardness, slope, and terrain roughness.
4. **Find Earth Analogs:** Use a nearest-neighbour matching approach to identify Earth locations with similar geological characteristics.
5. **Explore the Results:** View ranked Earth locations, similarity scores, and comparisons explaining the matches.

## Key Features

- Interactive 3D Mars and Earth globes
- Coordinate-based Mars exploration
- Four selectable drilling depth ranges
- Geological and environmental feature comparison
- Earth analog similarity rankings
- Configurable feature weights
- Data visualization and downloadable CSV results
- Model validation information
- Dedicated About page

## Data and Limitations

Planetary Twin is a research prototype developed for the NASA Space Apps Challenge.

The project is designed to work with geological and environmental datasets from Mars and Earth. Some generated matching outputs may include estimated or synthetic inputs and are identified accordingly.

Similarity scores indicate how closely locations match according to the selected features and available data. They do not independently establish that a site is suitable for a drilling mission.

## Our Team

Developed for the NASA Space Apps Challenge 2026 by:

- **Markus Clarke**
- **Waleed Diab**
- **Dominic Chwieros**
- **Archait Jain**
- **Jad Wehbi**

## Project Goal

We believe that exploring other planets starts with understanding our own.

Planetary Twin aims to connect planetary science with practical engineering by helping researchers identify Earth environments that can support the testing of future Mars exploration technologies.

**Find your Mars location. Discover its Earth twin.**

## How to Run the Project

Planetary Twin runs in your web browser using a local Python HTTP server. You do not need to install Streamlit or create a virtual environment to use the frontend.

### Prerequisites

- Python 3 installed on your computer
- Git (if cloning the repository)
- A modern web browser such as Chrome, Edge, or Firefox

### 1. Clone the Repository

Open a terminal and run:

```bash
git clone https://github.com/dominic-chw/SpaceApps2026.git
cd SpaceApps2026
```

If you already have the repository downloaded, simply open its main folder in your terminal.

### 2. Start the Local Server

From the root directory of the project, run:

**Windows:**

```bash
py -3.11 -m http.server 8000
```

**macOS / Linux:**

```bash
python3 -m http.server 8000
```

You should see a message similar to:

```text
Serving HTTP on ... port 8000
```

Keep this terminal open while using the application.

### 3. Open the Application

Open your browser and navigate to:

**http://localhost:8000/web/**

You can also visit the About page directly:

**http://localhost:8000/web/about.html**

### 4. Explore Mars

Once the application loads:

1. Rotate the Mars globe and select a location.
2. Choose a drilling depth.
3. Click **Find Earth Analogs**.
4. Explore the matching locations on the Earth globe.
5. Review geological comparisons and download results as CSV.

The frontend reads the existing matching results from the project's `output/` directory. Generating or updating these datasets requires running the separate Python data-processing pipeline.

### 5. Stop the Server

Return to your terminal and press `Ctrl + C`.

### Troubleshooting

If the website doesn't load, make sure the server was started from the repository's root directory and that the `web/index.html` file exists.

If Earth analog results aren't appearing, check that the required JSON files are available in `output/grid/`.

If port 8000 is already occupied, start the server on another port:

```bash
py -3.11 -m http.server 8080
```

Then open **http://localhost:8080/web/**.

**Note:** The local web server is used to display the interface and read available project files. It does not independently run the geological matching pipeline or optional AI and weather services.
