# Changelog

## [2.6.0](https://github.com/blb-ventures/strawberry-django-plus/compare/v2.5.0...v2.6.0) (2023-06-01)


### Features

* add description to enums from django choices ([#217](https://github.com/blb-ventures/strawberry-django-plus/issues/217)) ([4d640e7](https://github.com/blb-ventures/strawberry-django-plus/commit/4d640e7d5cb05ed9bac79743e291121d2a9e56fa))
* use a type's get_queryset for Relay connections if it defines one ([#215](https://github.com/blb-ventures/strawberry-django-plus/issues/215)) ([bb3af76](https://github.com/blb-ventures/strawberry-django-plus/commit/bb3af7675a175fc3b85eedef54464198d38613da))

## [2.5.0](https://github.com/blb-ventures/strawberry-django-plus/compare/v2.4.2...v2.5.0) (2023-05-29)


### Features

* expose `__version__` on the package ([de71277](https://github.com/blb-ventures/strawberry-django-plus/commit/de71277624f6537e3ad0a1552f12718cadba2e4d))
* **optimizer:** support custom QS for prefetches ([e7ae685](https://github.com/blb-ventures/strawberry-django-plus/commit/e7ae6855a62f882ce979dcc8368701ebe88f9c80))


### Bug Fixes

* fix django versioning on test actions ([7c59081](https://github.com/blb-ventures/strawberry-django-plus/commit/7c59081c954ecdba72ae1d6b204d710282d8f3ff))
* fix LICENSE author ([b3fa178](https://github.com/blb-ventures/strawberry-django-plus/commit/b3fa178978dfad7004f50f73f59e761dfbf1c100))
* fix missing checkout version ([147e41d](https://github.com/blb-ventures/strawberry-django-plus/commit/147e41d7063fdda01913810f79c51edaada2e868))
* run mkdocs with poetry ([7155e3a](https://github.com/blb-ventures/strawberry-django-plus/commit/7155e3aaa646d13612fc3754c0c1ce5bd8813669))


### Miscellaneous

* **deps:** bump requests from 2.30.0 to 2.31.0 ([f254a3b](https://github.com/blb-ventures/strawberry-django-plus/commit/f254a3b567b8953c5ef9350d77f4fa58e6eefd8c))
* **deps:** update dev dependencies ([cbfd781](https://github.com/blb-ventures/strawberry-django-plus/commit/cbfd78168bfee0966f9e018700b12216be13518f))
* modernize CI/CD scripts and use release-please for releases ([b6ec168](https://github.com/blb-ventures/strawberry-django-plus/commit/b6ec16879078379a88f68a6ec8633cf02e78c296))


### Continuous Integration

* add bootstrap-sha for release-please ([4a1a534](https://github.com/blb-ventures/strawberry-django-plus/commit/4a1a534fa6dbe6a119b2d89c6728f7808c5f78fc))
