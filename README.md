# Picterra QGIS plugin

This is a QGIS _web_ plugin built around the Picterra Public API.

## Features

### Settings setup
Selecting `Web / Picterra / Settings` from the Menu Bar will open a popup where you can check the QGIS settings and, more important, setup the interaction with the Picterra platform: it suffices to put your account API Key to unlock the power of Picterra in QGIS!

Plase note that any other feature of the plugin depend on the correct setup above: trying to upload or detect without a valid API key will just return an error message.

### Upload

#### Images
Either from plugins toolbar or the Menu Bar you can open a popup that will upload a given local image to your account, inside the "Picterra API Project".

#### Detection areas
Either from plugins toolbar or the Menu Bar you can open a popup that will upload a given GeoJSOn as the detection areas of an image.

### Detect
Either from plugins toolbar or the Menu Bar you can open a popup that will

## Contributing

### Architecture overview

We use [PyQt5](https://pypi.org/project/PyQt5/) binding Qt to Python.

To avoid installing HTTP clients (eg _requests_), we wrap around [qgis.PyQt.QtNetwork](https://qgis.org/pyqgis/3.4/core/QgsNetworkAccessManager.html), see [network.py](src/network.py).


### Code styleguide
Use [PEP 484](https://www.python.org/dev/peps/pep-0484/) type annotations plus [Google style](http://google.github.io/styleguide/pyguide.html) docstrings.


### Local development setup

We recommend to use a separate environment to develop the plugin, such as [conda](https://docs.conda.io/en/latest/), [poetry](https://github.com/python-poetry/poetry), [virtualenv](https://virtualenv.pypa.io/en/latest/) or [pipenv](https://pipenv.pypa.io/en/latest/).


#### Requirements

##### 1. Install QGIS and plugins

###### QGIS
Make sure you have the **QGIS** version [installed](https://www.qgis.org/en/site/forusers/download.html) on your OS: last working version (QGIS **3**) is _3.22.4-Białowieża_.

QGIS 3 installation needs **Python 3**, which is needed by the Picterra plugin, so you shold not have anything else to do in that sense.

###### Plugin Reloader
Install the **Plugin Reload** [plugin](https://plugins.qgis.org/plugins/plugin_reloader/) via the QGIS UI (see [here](https://docs.qgis.org/3.4/en/docs/training_manual/qgis_plugins/fetching_plugins.html)); the [QGIS Official Plugin Repository](https://plugins.qgis.org/plugins/) should have it without any further configuration.

###### Picterra Plugin
You then need the Picterra plugin install to be able to reload it: to do so either use the [current zip from master](https://cloud.picterra.ch/public/qgis-plugin/archives/picterra-alpha-2020-06-12.zip) or create a new one as per the [Building section below](#building).

##### 2. Plugin Builder Tool

You then need the Python package **pb_tool** [version 3.1.0](https://pypi.org/project/pb-tool/) on your environment, that you can install via eg [pip](https://pip.pypa.io/en/stable/)

```bash
pip install pb_tool
```

The tool will also be available via the **pbt** alias.

For the plugin to work, you also need `make`; the package is usually included by default in the O; if not, install it via eg `sudo apt install make`.

##### 3. Install other needed packages

You need to compile Qt files to Python, so install the [PyQt5 Auto Compiler ](https://pypi.org/project/pyqt5ac/):

```bash
pip install pyqt5ac
```

##### 4. Compile the Qt resources file

We need to compile the assets file to Python

```bash
pyrcc5 resources.qrc -o resources.py
```

This should generate a (git-ignored) `resources.py` file in the repo root.

##### 5. Discover the QGIS plugin path

In order to be able to deploy the current version of the plugin, you first need to disocver the QGIS plugin folder [location](https://gis.stackexchange.com/questions/274311/qgis-3-plugin-folder-location):

1. Open QGIS
2. From the topbar: `Settings -> User profiles -> Open active profile folder`
3. Your OS file manager should then open a folder
4. From there, you can go to `python -> plugins` (if you have no plugins yet, there will be no "plugins" folder, but you should at least have the "plugin reload one", see [above](#2-plugin-builder-tool))
5. That's the plugin folder for QGIS v3, so you can `pwd` to discover its full path; it should be something like `/home/foobar/.local/share/QGIS/QGIS3/profiles/default/python/plugins`.


Now every time you want to deploy your local version you can write `pbt deploy -p <QGGIS_PLUGIN_FOLDER_PATH>`

See `pbt deploy --help` for useful options.

Side note: if you wish better separation of environments, you can also create a [dedicated QGIS profile](https://docs.qgis.org/3.28/en/docs/user_manual/introduction/qgis_configuration.html#working-with-user-profiles) for developing the Picterra plugin; just remember to switch profiles when running the deploy, otherwise the new plugin version will be activated on a different profile and you won't have the latest version in QGIS.

#### Deploy the current pljugin

In order to deploy the plugin with the current code version you are working on, run

```bash
pbt deploy -p <QGGIS_PLUGIN_FOLDER_PATH> -y
```

in the plugin root directory (i.e. the one with the _src_ folder).

At this point, if you open QGIS or, if it's already opened, reload the Picterra plugin via "the reload plugin" plugin, you should be able to see your Picterra plugin updated.

If it does not happen, it could be due to conflicting versions from previous plugin installations: check that inside the profiles folder (`QGGIS_PLUGIN_FOLDER_PATH`) there is a "plugin" one with inside the updated code, but not a "picterra" folder (in case, you can safely delete it).

Please note that installing from zip could break it again.


##### Alternative way
There is also a manual way to deploy the plugin, following what is in the [building step](#building): once run
```bash
pbt zip
```

you can un zip the archive in the _plugins_ directory inside the profiles folder (`QGGIS_PLUGIN_FOLDER_PATH`). Then just use the plugin reloader from QGIS.

#### Targeting different servers

By default, the plugin will target (and therefore authenticate) towards the Picterra production environment. To change this, set the `QGIS_PICTERRA_API_BASE_URL` environment variable, using the `api_server` value in `metadata.txt` as reference.

In order to have the environment variable take effect, launch QGIS via the command line after having set it, eg

```bash
export QGIS_PICTERRA_API_BASE_URL=http://<YOUR_LOCAL_PICTERRA_IP>/public/api
qgis
```

To double-check that all is working, open the plugin settings in QGIS: the "API server" entry in the "Picterra account" tab should show the environment you have exposed before.

#### Debugging

##### Debugging tools
To better inspect what's happening, QGIS provides two useful panels

* **Debugging/Development Tools**
* **Log Messages**

that can be easily enlabed from the topbar via `View / Panels`.

##### Inspect messages

You can see the logging from the plugin in QGIS "Log Messages" panel; from there, you can see different "levels" of messages (as tabs of the panel):
 * the **Picterra plugin** is the most imporant one
 * **NetworkAccessManager** is extremely useful (even if very verbose) as well, since it records all the network traffic
 * **Python error** will show any error coming from the plugin (usually the UI should tell you that something went wrong as well)
 * sometimes looking at **General** and **Plugins** can be handy

To increase the QGIS logging verbosity set the `QGIS_PICTERRA_DEBUG` environment variable to 1.


##### New UI assets / files

TODO assets/...

#### Building

After editing the plugin, running

```bash
pbt zip
```

will create a **picterra.zip** archive in the **zip_build** directory.

You can then install this zip through QGIS plugin manager.

#### Publishing

Follow the [official QGIS plugin publishing instructions](https://plugins.qgis.org/publish/).

## Resources
 * [Writing a Python Plugin for QGIS3](https://www.qgistutorials.com/en/docs/3/building_a_python_plugin.html)
 * Picterra organization [GitHub repo](https://github.com/Picterra/picterra-qgis-plugin)
 * [Official Picterra API documentation](https://app.picterra.ch/public/apidocs/v2/)

## Credits
* [Plugin builder](https://plugins.qgis.org/plugins/pluginbuilder3/) ([GeoApt LLC](http://geoapt.net/))
* [pb_tool](http://g-sherman.github.io/plugin_build_tool/)
* QGIS Network access manager ([Boundless]( http://boundlessgeo.com)), see `src/network.py`
* [QGIS](https://github.com/qgis)
* [PyQGIS dev cookbok](https://docs.qgis.org/3.28/en/docs/pyqgis_developer_cookbook/index.html)
* [QGIS plugins web portal](https://plugins.qgis.org/)

