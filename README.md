# Picterra QGIS plugin

## Contributing

## Repository
[GitHub repo](https://github.com/Picterra/picterra-qgis-plugin)

## API reference
Please refer to the [Official Picterra API documentation](https://app.picterra.ch/public/apidocs/v1/).

### Documentation
Use [PEP 484](https://www.python.org/dev/peps/pep-0484/) type annotations plus [Google style](http://google.github.io/styleguide/pyguide.html) docstrings.


## Dev

### Plugin Builder

#### Results

Congratulations! You just built a plugin for QGIS!  

Your plugin **Picterra** was created in: **/home/andrea/Dev/qgis/picterra**

Your QGIS plugin directory is located at: **/home/andrea/.local/share/QGIS/QGIS3/profiles/default/python/plugins**

#### What's Next

1.  If resources.py is not present in your plugin directory, compile the resources file using pyrcc5 (simply use **pb_tool** or **make** if you have automake)
2.  Optionally, test the generated sources using **make test** (or run tests from your IDE)
3.  Copy the entire directory containing your new plugin to the QGIS plugin directory (see Notes below)
4.  Test the plugin by enabling it in the QGIS plugin manager
5.  Customize it by editing the implementation file **picterra.py**
6.  Create your own custom icon, replacing the default **icon.png**
7.  Modify your user interface by opening ***.ui** in Qt Designer

Notes:

*   You can use **pb_tool** to compile, deploy, and manage your plugin. Tweak the _pb_tool.cfg_ file included with your plugin as you add files. Install **pb_tool** using _pip_ or _easy_install_. See **http://loc8.cc/pb_tool** for more information.
*   You can also use the **Makefile** to compile and deploy when you make changes. This requires GNU make (gmake). The Makefile is ready to use, however you will have to edit it to add additional Python source files, dialogs, and translations.


For information on writing PyQGIS code, see **http://loc8.cc/pyqgis_resources** for a list of resources.

©2011-2020 GeoApt LLC - geoapt.com

## Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.1] - 2020-03-01
### Added
- flake8 configuration file for CI

## Credits
* [Plugin builder](https://plugins.qgis.org/plugins/pluginbuilder3/) ([GeoApt LLC](http://geoapt.net/))
* [pb_tool](http://g-sherman.github.io/plugin_build_tool/)
* QGIS Network access manager ([Boundless]( http://boundlessgeo.com))
* [QGIS](https://github.com/qgis)